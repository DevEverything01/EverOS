"""Concurrent ``update_episode`` calls never lose an update.

Before: the episode was read outside any lock and the whole entry written
back, so two patches of different fields racing on one episode left only the
later writer's field. Now the read is versioned and the write compares the
version under the file lock; the loser re-reads and re-applies its patch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from everos.core.errors import MemoryUpdateFailedError
from everos.infra.persistence import MemoryRoot
from everos.infra.persistence.markdown import MarkdownEpisodeStore
from everos.memory.models import Episode
from everos.memory.ports import NO_SOURCE_CHECK, Scope
from everos.service.memory._edit import EpisodePatch, update_episode

OWNER = "u1"
SCOPE = Scope(app_id="default", project_id="default", owner_id=OWNER, owner_type="user")


class _RacingStore(MarkdownEpisodeStore):
    """The real store; the first ``n`` reads wait for each other, so every
    caller reads the same version before any of them writes (a forced race)."""

    _racers = 0
    _barrier: asyncio.Barrier

    def race(self, racers: int) -> None:
        self._racers = racers
        if racers:
            self._barrier = asyncio.Barrier(racers)

    async def read_versioned(self, scope: Scope, entry_id: str) -> Any:
        got = await super().read_versioned(scope, entry_id)
        if self._racers > 0:
            self._racers -= 1
            await self._barrier.wait()
        return got

    async def read_many(self, scope: Scope, entry_ids: Any) -> Any:
        got = await super().read_many(scope, entry_ids)
        if self._racers > 0:  # the pre-versioning code path read with this
            self._racers -= 1
            await self._barrier.wait()
        return got


def _episode(text: str = "original") -> Episode:
    return Episode(
        owner_id=OWNER,
        episode=text,
        timestamp=1_747_216_800_000,
        subject="Chat",
        summary="before",
        session_id="s1",
        sender_ids=[OWNER],
        parent_id="mc_1",
    )


async def _seed(tmp_path: Path, racers: int) -> SimpleNamespace:
    root = MemoryRoot(tmp_path)
    root.ensure()
    store = _RacingStore(root)
    neighbour = await store.append(
        SCOPE, _episode("neighbour"), only_if=NO_SOURCE_CHECK
    )
    ref = await store.append(SCOPE, _episode(), only_if=NO_SOURCE_CHECK)
    store.race(racers)
    return SimpleNamespace(
        stores=SimpleNamespace(episode=store),
        store=store,
        ref=ref,
        neighbour=neighbour,
        memory_id=f"{OWNER}_{ref.entry_id}",
        path=tmp_path / ref.md_path,
    )


def _update(env: SimpleNamespace, **patch: str) -> Any:
    return update_episode(
        env.stores,
        app_id="default",
        project_id="default",
        memory_id=env.memory_id,
        patch=EpisodePatch(**patch),
    )


async def test_concurrent_patches_of_different_fields_both_land(
    tmp_path: Path,
) -> None:
    env = await _seed(tmp_path, racers=2)

    results = await asyncio.gather(
        _update(env, summary="new summary"), _update(env, subject="new subject")
    )

    assert [r.unchanged for r in results] == [False, False]
    (got,) = await env.store.read_many(SCOPE, [env.ref.entry_id])
    assert got.item.summary == "new summary"
    assert got.item.subject == "new subject"
    assert got.item.episode == "original"


async def test_unforced_gather_of_different_fields_both_land(tmp_path: Path) -> None:
    env = await _seed(tmp_path, racers=0)

    await asyncio.gather(
        _update(env, summary="s2"),
        _update(env, subject="t2"),
        _update(env, episode="e2"),
    )

    (got,) = await env.store.read_many(SCOPE, [env.ref.entry_id])
    assert (got.item.summary, got.item.subject, got.item.episode) == ("s2", "t2", "e2")


async def test_concurrent_patches_of_one_field_leave_one_whole_entry(
    tmp_path: Path,
) -> None:
    env = await _seed(tmp_path, racers=2)

    results = await asyncio.gather(_update(env, summary="A"), _update(env, summary="B"))

    assert [r.unchanged for r in results] == [False, False]
    got = await env.store.read_many(SCOPE, [env.ref.entry_id, env.neighbour.entry_id])
    by_id = {g.ref.entry_id: g.item for g in got}
    assert by_id[env.ref.entry_id].summary in {"A", "B"}
    assert by_id[env.ref.entry_id].subject == "Chat"
    assert by_id[env.ref.entry_id].episode == "original"
    assert by_id[env.neighbour.entry_id].episode == "neighbour"
    text = env.path.read_text()
    assert text.count(f"<!-- entry:{env.ref.entry_id} -->") == 1
    assert text.count(f"<!-- /entry:{env.ref.entry_id} -->") == 1
    assert text.startswith("---\n")


async def test_gives_up_after_three_conflicts(tmp_path: Path) -> None:
    env = await _seed(tmp_path, racers=0)
    store = env.store
    calls = 0
    real_replace = MarkdownEpisodeStore.replace

    async def interfering_replace(self, scope, entry_id, episode, **kw):  # type: ignore[no-untyped-def]
        # Another writer lands between every read and write of this caller.
        nonlocal calls
        calls += 1
        await real_replace(self, scope, entry_id, _episode(f"other {calls}"))
        return await real_replace(self, scope, entry_id, episode, **kw)

    store.replace = interfering_replace.__get__(store)

    with pytest.raises(MemoryUpdateFailedError):
        await _update(env, summary="never")

    assert calls == 3
    (got,) = await store.read_many(SCOPE, [env.ref.entry_id])
    assert got.item.summary == "before"
