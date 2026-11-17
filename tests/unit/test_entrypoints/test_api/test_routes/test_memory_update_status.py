"""``POST /memory/update`` status codes over a real markdown episode store.

400 bad id, 403 wrong owner, 404 no such episode, 500 rewrite failed; 200
with ``unchanged=true`` only when the patch equals what is stored. Before,
all of these were 200 + ``unchanged=true`` (and an owner with an underscore
could never be edited at all).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from everos.entrypoints.api.app import create_app
from everos.infra.persistence import MemoryRoot
from everos.infra.persistence.markdown import MarkdownEpisodeStore
from everos.memory.models import Episode
from everos.memory.ports import NO_SOURCE_CHECK, ReplaceOutcome, Scope
from everos.service.memory._edit import update_episode
from tests.fakes import make_application
from tests.fakes.application import StubMemoryService

OWNER = "user_123"
SCOPE = Scope(app_id="default", project_id="default", owner_id=OWNER, owner_type="user")


class _EditingMemory(StubMemoryService):
    """The stub memory facade, with the real update use case over ``stores``."""

    def __init__(self, stores: Any) -> None:
        super().__init__([])
        self._stores = stores

    async def update_episode(self, **kwargs: Any) -> Any:
        return await update_episode(self._stores, **kwargs)


@pytest.fixture
async def env(tmp_path: Path):
    root = MemoryRoot(tmp_path)
    root.ensure()
    store = MarkdownEpisodeStore(root)
    ref = await store.append(
        SCOPE,
        Episode(
            owner_id=OWNER,
            episode="original",
            timestamp=1_747_216_800_000,
            subject="Chat",
            summary="before",
            session_id="s1",
            sender_ids=[OWNER],
            parent_id="mc_1",
        ),
        only_if=NO_SOURCE_CHECK,
    )
    stores = SimpleNamespace(episode=store)
    app = create_app(
        application=make_application(root=tmp_path, memory=_EditingMemory(stores))
    )
    return SimpleNamespace(
        app=app,
        stores=stores,
        memory_id=f"{OWNER}_{ref.entry_id}",
        ref=ref,
        root=tmp_path,
    )


async def _update(env, memory_id: str, summary: str, **extra: Any):
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/v2/memory/update",
            json={
                "memory_type": "episode",
                "memory_id": memory_id,
                "patch": {"summary": summary},
                **extra,
            },
        )


async def test_underscored_owner_is_edited(env) -> None:
    response = await _update(env, env.memory_id, "after", user_id=OWNER)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == env.memory_id
    assert data["unchanged"] is False
    got = await env.stores.episode.read_many(SCOPE, [env.ref.entry_id])
    assert [g.item.summary for g in got] == ["after"]


async def test_same_content_is_200_unchanged(env) -> None:
    response = await _update(env, env.memory_id, "before")

    assert response.status_code == 200
    assert response.json()["data"]["unchanged"] is True


@pytest.mark.parametrize(
    "patch",
    [{}, {"summary": None}, {"episode": None, "summary": None, "subject": None}],
)
async def test_empty_patch_is_422(env, patch: dict) -> None:
    """Requesting nothing is a client error, not ``unchanged=true``."""
    before = (env.root / env.ref.md_path).read_text()
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v2/memory/update",
            json={"memory_type": "episode", "memory_id": env.memory_id, "patch": patch},
        )

    assert response.status_code == 422
    assert (env.root / env.ref.md_path).read_text() == before


async def test_invalid_id_is_400(env) -> None:
    response = await _update(env, "not-a-memory-id", "x")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.parametrize(
    "owner",
    ["..", "../user_123", "a/b", "/abs/path", "../../../other/default_project/users/x"],
)
async def test_path_like_owner_is_400(env, owner: str) -> None:
    """A memory id whose owner is a path, not a segment, never reaches the store."""
    before = (env.root / env.ref.md_path).read_text()

    response = await _update(env, f"{owner}_{env.ref.entry_id}", "pwned")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert (env.root / env.ref.md_path).read_text() == before


async def test_other_owner_is_403(env) -> None:
    response = await _update(env, env.memory_id, "x", user_id="someone_else")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_missing_episode_is_404(env) -> None:
    response = await _update(env, f"{OWNER}_ep_20991231_00000009", "x")

    assert response.status_code == 404


async def test_failed_rewrite_is_500(env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*_args: Any, **_kwargs: Any) -> ReplaceOutcome:
        return ReplaceOutcome.MISSING

    monkeypatch.setattr(env.stores.episode, "replace", _fail)

    response = await _update(env, env.memory_id, "x")

    assert response.status_code == 500


async def test_two_updates_in_a_row_both_land(env) -> None:
    first = await _update(env, env.memory_id, "one")
    second = await _update(env, env.memory_id, "two")

    assert (first.status_code, second.status_code) == (200, 200)
    got = await env.stores.episode.read_many(SCOPE, [env.ref.entry_id])
    assert [g.item.summary for g in got] == ["two"]
