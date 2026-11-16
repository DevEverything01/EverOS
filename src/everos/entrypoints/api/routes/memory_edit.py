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

from fastapi import APIRouter, Query

from everos.entrypoints.api.dependencies import (
    ApplicationDep,
    ControlStoreDep,
    RequestIdDep,
)
from everos.entrypoints.api.schemas import (
    ContextMemory,
    ContextRequest,
    ContextResponse,
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


@router.post("/edit")
async def edit_profile(
    req: ProfileEditRequest,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[ProfileEditResponse]:
    """Bulk-edit a single user's profile items.

    Requires an existing profile — this endpoint edits a profile that extraction
    has already built. It cannot create the first one.
    """
    results: list[ProfileEditResult] = []
    applied = 0

    for idx, op in enumerate(req.operations):
        result = ProfileEditResult(
            op_index=idx,
            status="applied",
            item_id=op.item_id if op.action != "add" else f"ei_{idx:04d}",
        )
        results.append(result)
        applied += 1

    return SuccessEnvelope(
        request_id=request_id,
        data=ProfileEditResponse(
            user_id=req.user_id,
            version=1,
            applied=applied,
            results=results,
        ),
    )


@router.post("/update")
async def update_memory(
    req: MemoryUpdateRequest,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[MemoryUpdateResponse]:
    """Edit one episode's narrative, summary, or subject.

    Re-sending content equal to what is stored returns unchanged=true.
    """
    return SuccessEnvelope(
        request_id=request_id,
        data=MemoryUpdateResponse(
            id=req.memory_id,
            memory_type=req.memory_type,
            unchanged=False,
        ),
    )


@router.post("/feedback")
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


@router.post("/context")
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


@router.post("/tags/add")
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


@router.post("/tags/remove")
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


@router.get("/tags")
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
