"""``update_episode`` refuses memory ids whose owner part is not a safe segment.

Before: the owner was ``.+`` and only ``.`` / ``..`` were refused, so
``../../../other/default_project/users/alice_ep_...`` parsed, joined into the
default scope's path and rewrote another app's episode. The owner now has to
come from the same alphabet ``/memory/delete_by_ids`` enforces, and no read or
write of an update ever resolves outside the memory root.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from everos.core.errors import InvalidMemoryIdError
from everos.infra.persistence import MemoryRoot
from everos.infra.persistence.markdown import MarkdownEpisodeStore
from everos.infra.persistence.markdown._base.reader import MarkdownReader
from everos.infra.persistence.markdown._base.writer import MarkdownWriter
from everos.memory.models import Episode
from everos.memory.ports import NO_SOURCE_CHECK, Scope
from everos.service.memory._edit import EpisodePatch, update_episode
from everos.service.memory._ids import split_episode_memory_id


def _episode(owner: str) -> Episode:
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
def touched(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Path]]:
    """Every path the markdown layer reads or writes (calls pass through)."""
    paths: list[Path] = []
    real_read = MarkdownReader.read_text
    real_write = MarkdownWriter.write_markdown

    async def read_text(path):  # type: ignore[no-untyped-def]
        paths.append(Path(path))
        return await real_read(path)

    async def write_markdown(self, path, *args, **kwargs):  # type: ignore[no-untyped-def]
        paths.append(Path(path))
        return await real_write(self, path, *args, **kwargs)

    monkeypatch.setattr(MarkdownReader, "read_text", staticmethod(read_text))
    monkeypatch.setattr(MarkdownWriter, "write_markdown", write_markdown)
    yield paths


@pytest.fixture
async def env(tmp_path: Path):
    root_dir = tmp_path / "root"
    root = MemoryRoot(root_dir)
    root.ensure()
    store = MarkdownEpisodeStore(root)
    victim_scope = Scope(
        app_id="other", project_id="default", owner_id="alice", owner_type="user"
    )
    victim = await store.append(
        victim_scope, _episode("alice"), only_if=NO_SOURCE_CHECK
    )
    victim_path = root_dir / victim.md_path
    return SimpleNamespace(
        root=root_dir,
        stores=SimpleNamespace(episode=store),
        victim=victim,
        victim_path=victim_path,
        victim_text=victim_path.read_text(),
    )


@pytest.mark.parametrize(
    "memory_id",
    [
        "../alice_ep_20261116_1",
        "a/b_ep_20261116_1",
        "a\\b_ep_20261116_1",
        ".._ep_20261116_1",
        "/etc/passwd_ep_20261116_1",
        "../../../other/default_project/users/alice_ep_20261116_1",
        "al ice_ep_20261116_1",
    ],
)
def test_split_rejects_unsafe_owner(memory_id: str) -> None:
    with pytest.raises(InvalidMemoryIdError):
        split_episode_memory_id(memory_id)


def test_split_keeps_dotted_and_email_owners() -> None:
    assert split_episode_memory_id("a.b@x+y-z_ep_20261116_1") == (
        "a.b@x+y-z",
        "ep_20261116_1",
    )


async def test_cross_app_traversal_is_refused(env, touched: list[Path]) -> None:
    memory_id = f"../../../other/default_project/users/alice_{env.victim.entry_id}"

    with pytest.raises(InvalidMemoryIdError):
        await update_episode(
            env.stores,
            app_id="default",
            project_id="default",
            memory_id=memory_id,
            patch=EpisodePatch(summary="pwned"),
        )

    assert env.victim_path.read_text() == env.victim_text
    assert touched == []


@pytest.mark.parametrize(
    "owner",
    ["..", "../../..", "/tmp/x", "a/b", "../../../../../outside"],
)
async def test_no_path_outside_root_is_touched(
    env, touched: list[Path], owner: str
) -> None:
    with pytest.raises(InvalidMemoryIdError):
        await update_episode(
            env.stores,
            app_id="default",
            project_id="default",
            memory_id=f"{owner}_ep_20261116_00000001",
            patch=EpisodePatch(summary="x"),
        )

    assert all(p.resolve().is_relative_to(env.root.resolve()) for p in touched)
    assert env.victim_path.read_text() == env.victim_text


async def test_legit_update_stays_inside_root(env, touched: list[Path]) -> None:
    result = await update_episode(
        env.stores,
        app_id="other",
        project_id="default",
        memory_id=f"alice_{env.victim.entry_id}",
        patch=EpisodePatch(summary="after"),
    )

    assert result.unchanged is False
    assert touched
    assert all(p.resolve().is_relative_to(env.root.resolve()) for p in touched)
