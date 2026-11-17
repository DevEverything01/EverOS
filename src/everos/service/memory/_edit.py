"""Memory editing operations: profile items and episode patches.

This module implements the ``/memory/edit`` and ``/memory/update`` use cases,
providing real persistence to markdown files through the existing stores.
Profile edits modify ``user.md``; episode updates rewrite one entry in place
in its daily episode file. Neither enqueues anything: the cascade's watcher
picks up the changed markdown and re-projects it into the index.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from everos.core.errors import (
    MemoryOwnerMismatchError,
    MemoryUpdateFailedError,
    NotFoundError,
)
from everos.core.observability.logging import get_logger
from everos.core.utils.datetime import get_utc_now, to_timestamp_ms
from everos.memory.models import (
    Episode,
    ExplicitInfoItem,
    ImplicitTraitItem,
    Profile,
)
from everos.memory.ports import ReplaceOutcome, Scope

from ._ids import split_episode_memory_id

if TYPE_CHECKING:
    from everos.memory.deps import Stores

logger = get_logger(__name__)

# Optimistic-concurrency rounds for one episode update: read + version,
# patch, compare-and-write; a lost race re-reads and re-applies the patch.
_UPDATE_ATTEMPTS = 3


@dataclass
class EditOperation:
    """One profile edit operation."""

    action: Literal["add", "update", "delete"]
    item_type: Literal["explicit_info", "implicit_traits"]
    category: str | None = None
    description: str | None = None
    evidence: str | None = None
    item_id: str | None = None
    reason: str | None = None


@dataclass
class EditResult:
    """Result of one edit operation."""

    op_index: int
    status: Literal["applied", "rejected"]
    item_id: str | None = None
    message: str | None = None


@dataclass
class ProfileEditResult:
    """Result of a profile edit batch."""

    user_id: str
    version: int
    applied: int
    results: list[EditResult]


@dataclass
class EpisodePatch:
    """Fields to update on an episode."""

    episode: str | None = None
    summary: str | None = None
    subject: str | None = None


@dataclass
class EpisodeUpdateResult:
    """Result of an episode update."""

    id: str
    memory_type: str = "episode"
    unchanged: bool = False
    edited_at: str | None = None


def _apply_patch(episode: Episode, patch: EpisodePatch) -> bool:
    """Apply ``patch`` to ``episode`` in place; whether anything changed."""
    changed = False
    for field in ("episode", "summary", "subject"):
        value = getattr(patch, field)
        if value is not None and value != getattr(episode, field):
            setattr(episode, field, value)
            changed = True
    return changed


def _mint_item_id(item_type: str) -> str:
    """Mint a fresh item_id for a profile item (ei_ or it_ prefix)."""
    prefix = "ei" if item_type == "explicit_info" else "it"
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def edit_profile(
    stores: Stores,
    *,
    app_id: str,
    project_id: str,
    user_id: str,
    operations: list[EditOperation],
) -> ProfileEditResult:
    """Apply a batch of profile item edits to a user's profile.

    Reads the existing profile, applies add/update/delete operations to the
    item lists, and writes the modified profile back through the same path
    the extractor uses. The cascade handles index sync.

    Args:
        stores: Write-side stores.
        app_id: Application scope.
        project_id: Project scope.
        user_id: User whose profile to edit.
        operations: Batch of edit operations.

    Returns:
        Result with per-operation status.
    """
    scope = Scope(
        app_id=app_id,
        project_id=project_id,
        owner_type="user",
        owner_id=user_id,
    )

    profile = await stores.profile.read(scope)
    if profile is None:
        now_ms = to_timestamp_ms(get_utc_now())
        profile = Profile(
            owner_id=user_id,
            summary="",
            timestamp=now_ms,
            explicit_info=[],
            implicit_traits=[],
        )

    results: list[EditResult] = []
    applied = 0

    for idx, op in enumerate(operations):
        result = _apply_operation(profile, idx, op)
        results.append(result)
        if result.status == "applied":
            applied += 1

    if applied > 0:
        profile.timestamp = to_timestamp_ms(get_utc_now())
        await stores.profile.write(scope, profile)
        logger.info(
            "profile_edited",
            user_id=user_id,
            applied=applied,
            total=len(operations),
        )

    return ProfileEditResult(
        user_id=user_id,
        version=1,
        applied=applied,
        results=results,
    )


def _apply_operation(profile: Profile, idx: int, op: EditOperation) -> EditResult:
    """Apply one edit operation to the profile in-place."""
    if op.item_type == "explicit_info":
        items = profile.explicit_info
    else:
        items = profile.implicit_traits

    if op.action == "add":
        if not op.description:
            return EditResult(
                op_index=idx,
                status="rejected",
                message="description required for add",
            )
        new_id = _mint_item_id(op.item_type)
        now_ms = to_timestamp_ms(get_utc_now())
        if op.item_type == "explicit_info":
            new_item = ExplicitInfoItem(
                description=op.description,
                category=op.category,
                evidence=op.evidence,
                observed_at=now_ms,
            )
        else:
            new_item = ImplicitTraitItem(
                description=op.description,
                trait=op.category,
                basis=op.evidence,
                observed_at=now_ms,
            )
        new_item.model_extra["item_id"] = new_id
        items.append(new_item)
        return EditResult(op_index=idx, status="applied", item_id=new_id)

    if op.action == "update":
        if not op.item_id:
            return EditResult(
                op_index=idx,
                status="rejected",
                message="item_id required for update",
            )
        for item in items:
            if item.model_extra.get("item_id") == op.item_id:
                if op.description is not None:
                    item.description = op.description
                if op.category is not None:
                    if op.item_type == "explicit_info":
                        item.category = op.category
                    else:
                        item.trait = op.category
                if op.evidence is not None:
                    if op.item_type == "explicit_info":
                        item.evidence = op.evidence
                    else:
                        item.basis = op.evidence
                item.observed_at = to_timestamp_ms(get_utc_now())
                return EditResult(op_index=idx, status="applied", item_id=op.item_id)
        return EditResult(
            op_index=idx,
            status="rejected",
            item_id=op.item_id,
            message="item not found",
        )

    if op.action == "delete":
        if not op.item_id:
            return EditResult(
                op_index=idx,
                status="rejected",
                message="item_id required for delete",
            )
        for i, item in enumerate(items):
            if item.model_extra.get("item_id") == op.item_id:
                items.pop(i)
                return EditResult(op_index=idx, status="applied", item_id=op.item_id)
        return EditResult(
            op_index=idx,
            status="rejected",
            item_id=op.item_id,
            message="item not found",
        )

    return EditResult(
        op_index=idx, status="rejected", message=f"unknown action: {op.action}"
    )


async def update_episode(
    stores: Stores,
    *,
    app_id: str,
    project_id: str,
    memory_id: str,
    patch: EpisodePatch,
    user_id: str | None = None,
) -> EpisodeUpdateResult:
    """Update an episode's summary, subject, or content.

    Locates the episode by its memory_id (which encodes owner_id and entry_id),
    applies the patch, and writes the modified entry back to its daily file.
    The read is versioned and the write compares that version under the
    file lock; when another writer got in between, the episode is re-read
    and the patch re-applied (up to ``_UPDATE_ATTEMPTS`` rounds), so two
    concurrent patches of different fields both land.

    Args:
        stores: Write-side stores.
        app_id: Application scope.
        project_id: Project scope.
        memory_id: Episode memory id (format: ``<owner_id>_<entry_id>``).
        patch: Fields to update.
        user_id: Optional owner guard - reject if episode belongs to another.

    Returns:
        Result with the edited episode id and timestamp; ``unchanged=True``
        only when the patch equals what is stored.

    Raises:
        InvalidMemoryIdError: ``memory_id`` is not an episode id (400).
        MemoryOwnerMismatchError: ``user_id`` is set and is not the owner (403).
        NotFoundError: no such episode (404).
        MemoryUpdateFailedError: the episode vanished while rewriting, or
            kept changing concurrently through every attempt (500).
    """
    owner_id, entry_id = split_episode_memory_id(memory_id)

    if user_id is not None and owner_id != user_id:
        raise MemoryOwnerMismatchError(
            f"memory {memory_id!r} does not belong to user {user_id!r}"
        )

    scope = Scope(
        app_id=app_id,
        project_id=project_id,
        owner_type="user",
        owner_id=owner_id,
    )

    for _attempt in range(_UPDATE_ATTEMPTS):
        versioned = await stores.episode.read_versioned(scope, entry_id)
        if versioned is None:
            raise NotFoundError(f"episode {memory_id!r} not found")
        episode = versioned.stored.item
        if not _apply_patch(episode, patch):
            return EpisodeUpdateResult(id=memory_id, unchanged=True)
        outcome = await stores.episode.replace(
            scope, entry_id, episode, expected_version=versioned.version
        )
        if outcome is ReplaceOutcome.REPLACED:
            break
        if outcome is ReplaceOutcome.MISSING:
            raise MemoryUpdateFailedError(
                f"episode {memory_id!r} could not be rewritten"
            )
        logger.info("episode_update_conflict", memory_id=memory_id, attempt=_attempt)
    else:
        raise MemoryUpdateFailedError(
            f"episode {memory_id!r} kept changing concurrently; "
            f"gave up after {_UPDATE_ATTEMPTS} attempts"
        )
    edited_at = get_utc_now().isoformat()

    logger.info("episode_updated", memory_id=memory_id, entry_id=entry_id)

    return EpisodeUpdateResult(
        id=memory_id,
        memory_type="episode",
        unchanged=False,
        edited_at=edited_at,
    )
