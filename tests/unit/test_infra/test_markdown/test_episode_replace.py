"""In-place episode rewrite: append -> replace -> read_many round trips.

``replace`` used to drop the ``<!-- entry:id -->`` markers and double the
``## `` header, so the entry vanished from ``read_many`` after one edit.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from everos.infra.persistence import MemoryRoot
from everos.infra.persistence.markdown import MarkdownEpisodeStore
from everos.memory.models import Episode
from everos.memory.ports import NO_SOURCE_CHECK, ReplaceOutcome, Scope

SCOPE = Scope(app_id="app", project_id="proj", owner_id="u1", owner_type="user")
TS = 1_747_216_800_000


def _episode(text: str, summary: str = "short") -> Episode:
    return Episode(
        owner_id="u1",
        episode=text,
        timestamp=TS,
        subject="Chat",
        summary=summary,
        session_id="s1",
        sender_ids=["u1"],
        parent_id="mc_1",
    )


@pytest.fixture
def store_and_root(tmp_path: Path) -> tuple[MarkdownEpisodeStore, MemoryRoot]:
    root = MemoryRoot(tmp_path)
    root.ensure()
    return MarkdownEpisodeStore(root), root


def _entry_block(text: str, entry_id: str) -> str:
    start = text.index(f"<!-- entry:{entry_id} -->")
    end = text.index(f"<!-- /entry:{entry_id} -->")
    return text[start:end]


async def test_append_replace_read_many_keeps_entry(store_and_root) -> None:
    store, root = store_and_root
    ref = await store.append(SCOPE, _episode("original"), only_if=NO_SOURCE_CHECK)

    outcome = await store.replace(SCOPE, ref.entry_id, _episode("edited"))
    assert outcome is ReplaceOutcome.REPLACED

    got = await store.read_many(SCOPE, [ref.entry_id])
    assert len(got) == 1
    assert got[0].ref.entry_id == ref.entry_id
    assert got[0].item.episode == "edited"
    text = (root.root / ref.md_path).read_text()
    assert f"<!-- entry:{ref.entry_id} -->" in text
    assert f"<!-- /entry:{ref.entry_id} -->" in text
    block_lines = _entry_block(text, ref.entry_id).splitlines()
    assert block_lines[1] == f"## {ref.entry_id}"
    assert "## ## " not in text


async def test_replace_preserves_frontmatter(store_and_root) -> None:
    store, root = store_and_root
    ref = await store.append(SCOPE, _episode("original"), only_if=NO_SOURCE_CHECK)
    path = root.root / ref.md_path
    before = path.read_text().split("---")[1]

    await store.replace(SCOPE, ref.entry_id, _episode("edited"))

    assert path.read_text().split("---")[1] == before


async def test_two_replaces_are_stable(store_and_root) -> None:
    store, root = store_and_root
    ref = await store.append(SCOPE, _episode("v0"), only_if=NO_SOURCE_CHECK)
    path = root.root / ref.md_path

    await store.replace(SCOPE, ref.entry_id, _episode("v1"))
    after_first = path.read_text()
    await store.replace(SCOPE, ref.entry_id, _episode("v1"))
    assert path.read_text() == after_first

    await store.replace(SCOPE, ref.entry_id, _episode("v2"))
    got = await store.read_many(SCOPE, [ref.entry_id])
    assert [g.item.episode for g in got] == ["v2"]
    assert path.read_text().count(f"<!-- entry:{ref.entry_id} -->") == 1


async def test_replace_leaves_neighbours_intact(store_and_root) -> None:
    store, _ = store_and_root
    refs = [
        await store.append(SCOPE, _episode(f"e{i}"), only_if=NO_SOURCE_CHECK)
        for i in range(3)
    ]

    await store.replace(SCOPE, refs[1].entry_id, _episode("middle edited"))

    got = await store.read_many(SCOPE, [r.entry_id for r in refs])
    by_id = {g.ref.entry_id: g.item.episode for g in got}
    assert by_id == {
        refs[0].entry_id: "e0",
        refs[1].entry_id: "middle edited",
        refs[2].entry_id: "e2",
    }


async def test_concurrent_edits_to_different_entries_both_land(
    store_and_root,
) -> None:
    store, _ = store_and_root
    refs = [
        await store.append(SCOPE, _episode(f"e{i}"), only_if=NO_SOURCE_CHECK)
        for i in range(4)
    ]

    results = await asyncio.gather(
        *(
            store.replace(SCOPE, r.entry_id, _episode(f"edited {i}"))
            for i, r in enumerate(refs)
        )
    )

    assert results == [ReplaceOutcome.REPLACED] * 4
    got = await store.read_many(SCOPE, [r.entry_id for r in refs])
    assert sorted(g.item.episode for g in got) == [f"edited {i}" for i in range(4)]


async def test_concurrent_versioned_edits_to_one_entry_have_one_winner(
    store_and_root,
) -> None:
    """Five writers that all read the same version: exactly one write lands,
    the other four are told ``CONFLICT`` and change nothing, and the stored
    entry is the winner's, framed once."""
    store, root = store_and_root
    ref = await store.append(SCOPE, _episode("v0"), only_if=NO_SOURCE_CHECK)
    read = await store.read_versioned(SCOPE, ref.entry_id)
    assert read is not None

    outcomes = await asyncio.gather(
        *(
            store.replace(
                SCOPE, ref.entry_id, _episode(f"v{i}"), expected_version=read.version
            )
            for i in range(1, 6)
        )
    )

    winners = [i for i, o in enumerate(outcomes, 1) if o is ReplaceOutcome.REPLACED]
    assert len(winners) == 1
    assert outcomes.count(ReplaceOutcome.CONFLICT) == 4
    got = await store.read_many(SCOPE, [ref.entry_id])
    assert [g.item.episode for g in got] == [f"v{winners[0]}"]
    text = (root.root / ref.md_path).read_text()
    assert text.count(f"<!-- entry:{ref.entry_id} -->") == 1
    assert text.count(f"<!-- /entry:{ref.entry_id} -->") == 1


async def test_version_changes_with_content_only(store_and_root) -> None:
    store, _ = store_and_root
    ref = await store.append(SCOPE, _episode("v0"), only_if=NO_SOURCE_CHECK)
    first = await store.read_versioned(SCOPE, ref.entry_id)
    await store.append(SCOPE, _episode("neighbour"), only_if=NO_SOURCE_CHECK)
    after_neighbour = await store.read_versioned(SCOPE, ref.entry_id)
    await store.replace(SCOPE, ref.entry_id, _episode("v1"))
    after_edit = await store.read_versioned(SCOPE, ref.entry_id)

    assert first is not None and after_neighbour is not None
    assert after_edit is not None
    assert first.version == after_neighbour.version
    assert after_edit.version != first.version
    assert await store.read_versioned(SCOPE, "ep_20991231_00000001") is None


async def test_replace_last_entry_without_trailing_newline(store_and_root) -> None:
    """The last entry's close marker is the file's last byte (hand-edited)."""
    store, root = store_and_root
    first = await store.append(SCOPE, _episode("e0"), only_if=NO_SOURCE_CHECK)
    last = await store.append(SCOPE, _episode("e1"), only_if=NO_SOURCE_CHECK)
    path = root.root / last.md_path
    path.write_text(path.read_text().rstrip("\n"))
    assert path.read_text().endswith(f"<!-- /entry:{last.entry_id} -->")
    front = path.read_text().split("---")[1]

    outcome = await store.replace(SCOPE, last.entry_id, _episode("e1 edited"))

    assert outcome is ReplaceOutcome.REPLACED
    got = await store.read_many(SCOPE, [first.entry_id, last.entry_id])
    assert {g.ref.entry_id: g.item.episode for g in got} == {
        first.entry_id: "e0",
        last.entry_id: "e1 edited",
    }
    text = path.read_text()
    assert text.split("---")[1] == front
    assert text.count(f"<!-- entry:{last.entry_id} -->") == 1
    assert text.count(f"<!-- /entry:{last.entry_id} -->") == 1
    assert text.rstrip().endswith(f"<!-- /entry:{last.entry_id} -->")


async def test_concurrent_append_and_replace(store_and_root) -> None:
    store, _ = store_and_root
    ref = await store.append(SCOPE, _episode("v0"), only_if=NO_SOURCE_CHECK)

    _, new_ref = await asyncio.gather(
        store.replace(SCOPE, ref.entry_id, _episode("edited")),
        store.append(SCOPE, _episode("later"), only_if=NO_SOURCE_CHECK),
    )

    got = await store.read_many(SCOPE, [ref.entry_id, new_ref.entry_id])
    assert {g.ref.entry_id: g.item.episode for g in got} == {
        ref.entry_id: "edited",
        new_ref.entry_id: "later",
    }
