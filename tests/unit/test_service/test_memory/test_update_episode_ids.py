"""``update_episode``: owner ids with underscores, and explicit failures.

Before: the id was split at the first ``_``, so ``user_123_ep_...`` looked up
owner ``user`` and returned ``unchanged=True`` -- the edit silently vanished.
Every failure (bad id, wrong owner, missing episode, failed rewrite) was also
reported as ``unchanged=True``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from everos.core.errors import (
    InvalidMemoryIdError,
    MemoryOwnerMismatchError,
    MemoryUpdateFailedError,
    NotFoundError,
)
from everos.infra.persistence import MemoryRoot
from everos.infra.persistence.markdown import MarkdownEpisodeStore
from everos.memory.models import Episode
from everos.memory.ports import NO_SOURCE_CHECK, ReplaceOutcome, Scope
from everos.service.memory._edit import EpisodePatch, update_episode
from everos.service.memory._ids import split_episode_memory_id

OWNER = "user_123"


def _scope(owner: str = OWNER) -> Scope:
    return Scope(
        app_id="default", project_id="default", owner_id=owner, owner_type="user"
    )


def _episode(owner: str = OWNER) -> Episode:
    return Episode(
        owner_id=owner,
        episode="original",
        timestamp=1_747_216_800_000,
        subject="Chat",
        summary="before",
        session_id="s1",
        sender_ids=[owner],
        parent_id="mc_1",
    )


@pytest.fixture
async def seeded(tmp_path: Path):
    root = MemoryRoot(tmp_path)
    root.ensure()
    store = MarkdownEpisodeStore(root)
    ref = await store.append(_scope(), _episode(), only_if=NO_SOURCE_CHECK)
    return SimpleNamespace(episode=store), f"{OWNER}_{ref.entry_id}", ref.entry_id


@pytest.mark.parametrize(
    ("memory_id", "owner", "entry"),
    [
        ("user_123_ep_20261116_00000001", "user_123", "ep_20261116_00000001"),
        ("u1_ep_20261116_7", "u1", "ep_20261116_7"),
        ("a_b_c_ep_20200101_1", "a_b_c", "ep_20200101_1"),
    ],
)
def test_split_keeps_underscored_owner(memory_id: str, owner: str, entry: str) -> None:
    assert split_episode_memory_id(memory_id) == (owner, entry)


@pytest.mark.parametrize(
    "memory_id",
    ["nounderscore", "user_123", "user_ep_2026_1", "._ep_20261116_1", "_ep_20261116_1"],
)
def test_split_rejects_non_episode_ids(memory_id: str) -> None:
    with pytest.raises(InvalidMemoryIdError):
        split_episode_memory_id(memory_id)


async def test_underscored_owner_edit_is_persisted(seeded) -> None:
    stores, memory_id, entry_id = seeded

    result = await update_episode(
        stores,
        app_id="default",
        project_id="default",
        memory_id=memory_id,
        patch=EpisodePatch(summary="after"),
        user_id=OWNER,
    )

    assert result.unchanged is False
    assert result.edited_at is not None
    got = await stores.episode.read_many(_scope(), [entry_id])
    assert [g.item.summary for g in got] == ["after"]


async def test_same_content_is_unchanged(seeded) -> None:
    stores, memory_id, _ = seeded

    result = await update_episode(
        stores,
        app_id="default",
        project_id="default",
        memory_id=memory_id,
        patch=EpisodePatch(summary="before"),
    )

    assert result.unchanged is True


async def test_invalid_id_raises(seeded) -> None:
    stores, _, _ = seeded

    with pytest.raises(InvalidMemoryIdError):
        await update_episode(
            stores,
            app_id="default",
            project_id="default",
            memory_id="garbage",
            patch=EpisodePatch(summary="x"),
        )


async def test_owner_mismatch_raises(seeded) -> None:
    stores, memory_id, _ = seeded

    with pytest.raises(MemoryOwnerMismatchError):
        await update_episode(
            stores,
            app_id="default",
            project_id="default",
            memory_id=memory_id,
            patch=EpisodePatch(summary="x"),
            user_id="someone_else",
        )


async def test_missing_episode_raises(seeded) -> None:
    stores, _, _ = seeded

    with pytest.raises(NotFoundError):
        await update_episode(
            stores,
            app_id="default",
            project_id="default",
            memory_id=f"{OWNER}_ep_20991231_00000009",
            patch=EpisodePatch(summary="x"),
        )


async def test_failed_rewrite_raises(seeded) -> None:
    stores, memory_id, _ = seeded
    real = stores.episode

    class _Vanishing:
        read_versioned = real.read_versioned

        async def replace(self, *_args, **_kwargs) -> ReplaceOutcome:
            return ReplaceOutcome.MISSING

    with pytest.raises(MemoryUpdateFailedError):
        await update_episode(
            SimpleNamespace(episode=_Vanishing()),
            app_id="default",
            project_id="default",
            memory_id=memory_id,
            patch=EpisodePatch(summary="x"),
        )
