"""Request / response DTOs for multi knowledge base endpoints.

These endpoints provide Cloud-parity multi-KB support:
- POST/GET/DELETE /knowledge_bases — CRUD for knowledge bases
- POST /knowledge_bases/{kb_id}/documents — upload a document
- GET /knowledge_bases/{kb_id}/documents — list documents
- GET /knowledge_bases/{kb_id}/documents/{doc_id} — get document detail
- GET /knowledge_bases/{kb_id}/categories — list categories
- GET /knowledge_bases/{kb_id}/topics/{topic_id} — get topic detail
- POST /knowledge_bases/{kb_id}/search — search knowledge

Legacy /knowledge/* endpoints are aliased to the "default" base.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .ids import AppScopeId, ProjectScopeId


class KnowledgeBaseCreateRequest(BaseModel):
    """Create a new knowledge base."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    app_id: AppScopeId = Field(default="default", min_length=1, max_length=128)
    project_id: ProjectScopeId = Field(default="default", min_length=1, max_length=128)


class KnowledgeBaseInfo(BaseModel):
    """Knowledge base info."""

    kb_id: str
    name: str
    description: str | None = None
    document_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class KnowledgeBaseCreateResponse(BaseModel):
    """Response for knowledge base creation."""

    kb_id: str
    name: str


class KnowledgeBaseListResponse(BaseModel):
    """List of knowledge bases."""

    items: list[KnowledgeBaseInfo]
    total: int


class KBDocumentCreateRequest(BaseModel):
    """Create a document via JSON body (alternative to multipart)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=500000)
    category: str | None = Field(default=None, max_length=100)


class KBDocumentItem(BaseModel):
    """Document item in a list."""

    doc_id: str
    title: str
    category_id: str | None = None
    topic_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class KBDocumentListResponse(BaseModel):
    """List of documents in a knowledge base."""

    items: list[KBDocumentItem]
    total: int
    page: int
    page_size: int


class KBTopicOverview(BaseModel):
    """Topic overview in a document."""

    topic_id: str
    title: str | None = None
    depth: int = 0


class KBDocumentDetail(BaseModel):
    """Full document detail."""

    doc_id: str
    title: str
    category_id: str | None = None
    summary: str | None = None
    topic_count: int = 0
    topics: list[KBTopicOverview] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class KBTopicDetail(BaseModel):
    """Full topic detail."""

    topic_id: str
    doc_id: str
    title: str | None = None
    content: str | None = None
    depth: int = 0


class KBCategoryItem(BaseModel):
    """Category in a knowledge base."""

    category_id: str
    description: str | None = None
    document_count: int = 0


class KBCategoryListResponse(BaseModel):
    """List of categories in a knowledge base."""

    categories: list[KBCategoryItem]


class KBSearchRequest(BaseModel):
    """Search within a knowledge base."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    method: Literal["keyword", "vector", "hybrid"] = "hybrid"
    top_k: Annotated[int, Field(ge=1, le=100)] = 10
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    include_content: bool = False


class KBSearchHit(BaseModel):
    """A search hit in a knowledge base."""

    topic_id: str
    doc_id: str
    doc_title: str | None = None
    title: str | None = None
    content: str | None = None
    score: float
    depth: int = 0


class KBSearchResponse(BaseModel):
    """Search response from a knowledge base."""

    topics: list[KBSearchHit]
    total: int
