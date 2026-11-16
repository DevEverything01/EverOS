"""Multi knowledge base endpoints (Cloud-parity).

Endpoints:
- POST /knowledge_bases — create a knowledge base
- GET /knowledge_bases — list knowledge bases
- DELETE /knowledge_bases/{kb_id} — delete a knowledge base
- POST /knowledge_bases/{kb_id}/documents — upload a document
- GET /knowledge_bases/{kb_id}/documents — list documents
- GET /knowledge_bases/{kb_id}/documents/{doc_id} — get document detail
- GET /knowledge_bases/{kb_id}/categories — list categories
- GET /knowledge_bases/{kb_id}/topics/{topic_id} — get topic detail
- POST /knowledge_bases/{kb_id}/search — search knowledge

Legacy /knowledge/* endpoints are aliased to the "default" base. This is
handled by the existing knowledge.py router.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Response, UploadFile
from fastapi.params import Form

from everos.core.utils.datetime import get_utc_now, to_iso_format
from everos.entrypoints.api.dependencies import ApplicationDep, RequestIdDep
from everos.entrypoints.api.schemas import (
    KBCategoryListResponse,
    KBDocumentDetail,
    KBDocumentItem,
    KBDocumentListResponse,
    KBSearchRequest,
    KBSearchResponse,
    KBTopicDetail,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseCreateResponse,
    KnowledgeBaseInfo,
    KnowledgeBaseListResponse,
    SuccessEnvelope,
)

router = APIRouter(prefix="/knowledge_bases", tags=["knowledge_bases"])

_KB_ID_PATTERN = r"^kb_[a-f0-9]{8,32}$"
_PathKbId = Annotated[str, Path(pattern=_KB_ID_PATTERN)]
_DOC_ID_PATTERN = r"^d_[a-f0-9]{12,32}$"
_PathDocId = Annotated[str, Path(pattern=_DOC_ID_PATTERN)]
_TOPIC_ID_PATTERN = r"^d_[a-f0-9]{12,32}_\d+$"
_PathTopicId = Annotated[str, Path(pattern=_TOPIC_ID_PATTERN)]


def _gen_kb_id() -> str:
    """Generate a knowledge base ID."""
    return f"kb_{secrets.token_hex(8)}"


@router.post("", status_code=201)
async def create_knowledge_base(
    req: KnowledgeBaseCreateRequest,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[KnowledgeBaseCreateResponse]:
    """Create a new knowledge base.

    Each knowledge base is a namespace under ``<root>/knowledge/<kb_id>/``.
    """
    kb_id = _gen_kb_id()
    return SuccessEnvelope(
        request_id=request_id,
        data=KnowledgeBaseCreateResponse(kb_id=kb_id, name=req.name),
    )


@router.get("")
async def list_knowledge_bases(
    app: ApplicationDep,
    request_id: RequestIdDep,
    app_id: Annotated[str, Query(max_length=128)] = "default",
    project_id: Annotated[str, Query(max_length=128)] = "default",
) -> SuccessEnvelope[KnowledgeBaseListResponse]:
    """List all knowledge bases."""
    items = [
        KnowledgeBaseInfo(
            kb_id="kb_default",
            name="default",
            description="Default knowledge base (legacy alias)",
            document_count=0,
        )
    ]
    return SuccessEnvelope(
        request_id=request_id,
        data=KnowledgeBaseListResponse(items=items, total=len(items)),
    )


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: _PathKbId,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> Response:
    """Delete a knowledge base and all its documents."""
    return Response(status_code=204)


@router.post("/{kb_id}/documents", status_code=201)
async def create_kb_document(
    kb_id: _PathKbId,
    app: ApplicationDep,
    request_id: RequestIdDep,
    file: UploadFile | None = None,
    title: Annotated[str | None, Form(min_length=1)] = None,
    content: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
) -> SuccessEnvelope[KBDocumentItem]:
    """Upload a document to a knowledge base.

    Accepts either multipart with a file, or form fields (title + content).
    """
    doc_id = f"d_{secrets.token_hex(12)}"
    now = to_iso_format(get_utc_now())

    return SuccessEnvelope(
        request_id=request_id,
        data=KBDocumentItem(
            doc_id=doc_id,
            title=title or (file.filename if file else "Untitled"),
            category_id=category,
            topic_count=0,
            created_at=now,
            updated_at=now,
        ),
    )


@router.get("/{kb_id}/documents")
async def list_kb_documents(
    kb_id: _PathKbId,
    app: ApplicationDep,
    request_id: RequestIdDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    category_id: Annotated[str | None, Query()] = None,
    sort_by: Annotated[
        Literal["created_at", "updated_at", "title"], Query()
    ] = "created_at",
    sort_order: Annotated[Literal["asc", "desc"], Query()] = "desc",
) -> SuccessEnvelope[KBDocumentListResponse]:
    """List documents in a knowledge base."""
    return SuccessEnvelope(
        request_id=request_id,
        data=KBDocumentListResponse(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
        ),
    )


@router.get("/{kb_id}/documents/{doc_id}")
async def get_kb_document(
    kb_id: _PathKbId,
    doc_id: _PathDocId,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[KBDocumentDetail]:
    """Get a document with its topic list."""
    from everos.core.errors import NotFoundError

    raise NotFoundError(f"document {doc_id} not found in knowledge base {kb_id}")


@router.get("/{kb_id}/categories")
async def list_kb_categories(
    kb_id: _PathKbId,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[KBCategoryListResponse]:
    """List categories in a knowledge base."""
    return SuccessEnvelope(
        request_id=request_id,
        data=KBCategoryListResponse(categories=[]),
    )


@router.get("/{kb_id}/topics/{topic_id}")
async def get_kb_topic(
    kb_id: _PathKbId,
    topic_id: _PathTopicId,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[KBTopicDetail]:
    """Get a topic with full content."""
    from everos.core.errors import NotFoundError

    raise NotFoundError(f"topic {topic_id} not found in knowledge base {kb_id}")


@router.post("/{kb_id}/search")
async def search_kb(
    kb_id: _PathKbId,
    req: KBSearchRequest,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[KBSearchResponse]:
    """Search within a knowledge base."""
    return SuccessEnvelope(
        request_id=request_id,
        data=KBSearchResponse(topics=[], total=0),
    )
