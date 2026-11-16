"""Memory editing operations: profile items and episode patches.

This module implements the ``/memory/edit`` and ``/memory/update`` use cases,
providing real persistence to markdown files through the existing stores.
Profile edits modify ``user.md``; episode updates modify daily episode files.
Both trigger the cascade's index sync the same way extraction-time writes do.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from everos.core.observability.logging import get_logger
from everos.core.utils.datetime import get_utc_now, to_timestamp_ms
from everos.memory.models import ExplicitInfoItem, ImplicitTraitItem, Profile
from everos.memory.ports import NO_SOURCE_CHECK, Scope

if TYPE_CHECKING:
    from everos.memory.deps import Stores

logger = get_logger(__name__)


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

    Args:
        stores: Write-side stores.
        app_id: Application scope.
        project_id: Project scope.
        memory_id: Episode memory id (format: ``<owner_id>_<entry_id>``).
        patch: Fields to update.
        user_id: Optional owner guard - reject if episode belongs to another.

    Returns:
        Result with the edited episode id and timestamp.
    """
    if "_" not in memory_id:
        return EpisodeUpdateResult(id=memory_id, unchanged=True)

    owner_id, entry_id = memory_id.split("_", 1)

    if user_id is not None and owner_id != user_id:
        return EpisodeUpdateResult(id=memory_id, unchanged=True)

    scope = Scope(
        app_id=app_id,
        project_id=project_id,
        owner_type="user",
        owner_id=owner_id,
    )

    stored_list = await stores.episode.read_many(scope, [entry_id])
    if not stored_list:
        return EpisodeUpdateResult(id=memory_id, unchanged=True)

    stored = stored_list[0]
    episode = stored.item

    changed = False
    if patch.episode is not None and patch.episode != episode.episode:
        episode.episode = patch.episode
        changed = True
    if patch.summary is not None and patch.summary != episode.summary:
        episode.summary = patch.summary
        changed = True
    if patch.subject is not None and patch.subject != episode.subject:
        episode.subject = patch.subject
        changed = True

    if not changed:
        return EpisodeUpdateResult(id=memory_id, unchanged=True)

    removed = await stores.episode.remove(scope, [entry_id])
    if not removed:
        return EpisodeUpdateResult(id=memory_id, unchanged=True)

    ref = await stores.episode.append(scope, episode, only_if=NO_SOURCE_CHECK)
    edited_at = get_utc_now().isoformat()

    logger.info(
        "episode_updated",
        memory_id=memory_id,
        new_entry_id=ref.entry_id if ref else None,
    )

    return EpisodeUpdateResult(
        id=ref.id if ref else memory_id,
        memory_type="episode",
        unchanged=False,
        edited_at=edited_at,
    )
