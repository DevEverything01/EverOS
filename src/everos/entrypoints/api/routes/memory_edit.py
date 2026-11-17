"""Memory editing, feedback, context, and tags endpoints (Cloud-parity).

Endpoints:
- POST /memory/edit — bulk-edit a user's profile items
- POST /memory/update — edit one episode's narrative/summary/subject
- POST /memory/feedback — rate a memory
- POST /memory/context — multi-round LLM-driven retrieval (stub)
- POST /memory/tags/add — add tags to a memory
- POST /memory/tags/remove — remove tags from a memory
- GET /memory/tags — list tags for a memory or memories for a tag

All write endpoints require ``memory:write`` scope.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from everos.entrypoints.api.dependencies import (
    ApplicationDep,
    ControlStoreDep,
    RequestIdDep,
    require_read,
    require_write,
)
from everos.entrypoints.api.schemas import (
    ContextMemory,
    ContextRequest,
    ContextResponse,
    FeedbackItem,
    FeedbackListResponse,
    FeedbackRequest,
    FeedbackResponse,
    MemoryUpdateRequest,
    MemoryUpdateResponse,
    ProfileEditRequest,
    ProfileEditResponse,
    ProfileEditResult,
    SuccessEnvelope,
    TagsAddRequest,
    TagsListResponse,
    TagsRemoveRequest,
    TagsResponse,
)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/edit", dependencies=[Depends(require_write)])
async def edit_profile(
    req: ProfileEditRequest,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[ProfileEditResponse]:
    """Bulk-edit a single user's profile items.

    Persists profile item add/update/delete into the user's ``user.md`` through
    the same repository/port the extractor uses, so md and the LanceDB/sqlite
    index stay consistent and the cascade enqueues the change.
    """
    from everos.service.memory import EditOperation as SvcEditOp

    operations = [
        SvcEditOp(
            action=op.action,
            item_type=op.type,
            category=op.data.category if op.data else None,
            description=op.data.description if op.data else None,
            evidence=op.data.evidence if op.data else None,
            item_id=op.item_id,
            reason=op.reason,
        )
        for op in req.operations
    ]

    result = await app.memory.edit_profile(
        app_id=req.app_id,
        project_id=req.project_id,
        user_id=req.user_id,
        operations=operations,
    )

    return SuccessEnvelope(
        request_id=request_id,
        data=ProfileEditResponse(
            user_id=result.user_id,
            version=result.version,
            applied=result.applied,
            results=[
                ProfileEditResult(
                    op_index=r.op_index,
                    status=r.status,
                    item_id=r.item_id,
                    message=r.message,
                )
                for r in result.results
            ],
        ),
    )


@router.post("/update", dependencies=[Depends(require_write)])
async def update_memory(
    req: MemoryUpdateRequest,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[MemoryUpdateResponse]:
    """Edit one episode's narrative, summary, or subject.

    Rewrites one episode entry's summary/subject/content in place in its
    daily ``episode-YYYY-MM-DD.md``, keeping its id, entry markers and
    front-matter. Nothing is enqueued: the markdown write is all this route
    does, and the cascade's file watcher re-projects the changed file into
    the index like any other markdown edit. A concurrent edit of the same
    episode is detected and the patch re-applied, so neither is lost. At
    least one patch field must be set (422 otherwise); unchanged=true means
    the requested values already equal what is stored.
    """
    from everos.service.memory import EpisodePatch

    patch = EpisodePatch(
        episode=req.patch.episode,
        summary=req.patch.summary,
        subject=req.patch.subject,
    )

    result = await app.memory.update_episode(
        app_id=req.app_id,
        project_id=req.project_id,
        memory_id=req.memory_id,
        patch=patch,
        user_id=req.user_id,
    )

    return SuccessEnvelope(
        request_id=request_id,
        data=MemoryUpdateResponse(
            id=result.id,
            memory_type=result.memory_type,
            unchanged=result.unchanged,
            edited_at=result.edited_at,
        ),
    )


@router.post("/feedback", dependencies=[Depends(require_write)])
async def add_feedback(
    req: FeedbackRequest,
    store: ControlStoreDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[FeedbackResponse]:
    """Record that a memory was right or wrong.

    Recording is not applying: the memory is not updated, deleted, hidden
    or re-indexed. To act on a rating, call update, delete_by_ids or edit.
    """
    feedback_id = await store.add_feedback(
        memory_id=req.memory_id,
        rating=1 if req.rating == "positive" else -1,
        user_id=None,
        comment=req.note,
    )
    return SuccessEnvelope(
        request_id=request_id,
        data=FeedbackResponse(id=feedback_id),
    )


@router.get("/feedback", dependencies=[Depends(require_read)])
async def get_feedback(
    store: ControlStoreDep,
    request_id: RequestIdDep,
    memory_id: Annotated[str, Query(min_length=1, max_length=256)],
) -> SuccessEnvelope[FeedbackListResponse]:
    """Get all feedback for a memory.

    Returns feedback records for the specified memory, newest first.
    """
    from everos.core.utils.datetime import to_iso_format

    rows = await store.get_feedback(memory_id)
    items = [
        FeedbackItem(
            id=row.id,
            memory_id=row.memory_id,
            rating=row.rating,
            comment=row.comment,
            created_at=to_iso_format(row.created_at) or "",
        )
        for row in rows
    ]
    return SuccessEnvelope(
        request_id=request_id,
        data=FeedbackListResponse(items=items),
    )


@router.post("/context", dependencies=[Depends(require_read)])
async def get_context(
    req: ContextRequest,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[ContextResponse]:
    """Multi-round LLM-driven retrieval.

    Returns memories, not an answer. Use for multi-hop, temporal and
    cross-conversation questions; use search for everything else.

    This is a stub implementation that delegates to search and assembles
    the results into a context string.
    """
    from everos.service import SearchRequest

    search_req = SearchRequest(
        query=req.query,
        user_id=req.user_id,
        method="hybrid",
        top_k=req.top_k,
        app_id=req.app_id,
        project_id=req.project_id,
    )
    search_resp = await app.memory.search(search_req)

    memories: list[ContextMemory] = []
    context_parts: list[str] = []

    for ep in search_resp.data.episodes or []:
        text = ep.episode or ep.summary or ""
        memories.append(
            ContextMemory(
                id=ep.id,
                kind="episode",
                score=ep.score or 0.0,
                text=text,
            )
        )
        context_parts.append(text)

    for profile in search_resp.data.profiles or []:
        text = str(profile.profile_data) if profile.profile_data else ""
        memories.append(
            ContextMemory(
                id=profile.id,
                kind="profile",
                score=0.0,
                text=text,
            )
        )
        context_parts.append(text)

    context = "\n\n".join(context_parts)
    tokens_estimate = len(context) // 4

    return SuccessEnvelope(
        request_id=request_id,
        data=ContextResponse(
            context=context,
            memories=memories,
            tokens_estimate=tokens_estimate,
        ),
    )


@router.post("/tags/add", dependencies=[Depends(require_write)])
async def add_tags(
    req: TagsAddRequest,
    store: ControlStoreDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[TagsResponse]:
    """Add tags to a memory.

    Tags are created by use. Idempotent: re-adding existing tags is harmless.
    """
    count = await store.add_tags(
        memory_id=req.memory_id,
        tags=req.tags,
        user_id=None,
    )
    return SuccessEnvelope(
        request_id=request_id,
        data=TagsResponse(memory_id=req.memory_id, count=count),
    )


@router.post("/tags/remove", dependencies=[Depends(require_write)])
async def remove_tags(
    req: TagsRemoveRequest,
    store: ControlStoreDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[TagsResponse]:
    """Remove tags from a memory.

    Removing non-existent tags is harmless.
    """
    count = await store.remove_tags(
        memory_id=req.memory_id,
        tags=req.tags,
    )
    return SuccessEnvelope(
        request_id=request_id,
        data=TagsResponse(memory_id=req.memory_id, count=count),
    )


@router.get("/tags", dependencies=[Depends(require_read)])
async def list_tags(
    store: ControlStoreDep,
    request_id: RequestIdDep,
    memory_id: Annotated[str | None, Query(max_length=256)] = None,
    tag: Annotated[str | None, Query(max_length=32)] = None,
) -> SuccessEnvelope[TagsListResponse]:
    """List tags for a memory, or memory ids with a specific tag.

    Provide exactly one of memory_id or tag.
    """
    if memory_id and tag:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail="provide exactly one of memory_id or tag",
        )

    if memory_id:
        tags = await store.get_tags(memory_id)
        return SuccessEnvelope(
            request_id=request_id,
            data=TagsListResponse(tags=tags, memory_ids=None),
        )

    if tag:
        memory_ids = await store.get_memories_by_tag(tag)
        return SuccessEnvelope(
            request_id=request_id,
            data=TagsListResponse(tags=None, memory_ids=memory_ids),
        )

    from fastapi import HTTPException

    raise HTTPException(
        status_code=422,
        detail="provide exactly one of memory_id or tag",
    )
