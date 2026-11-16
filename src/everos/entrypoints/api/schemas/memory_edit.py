"""Request / response DTOs for memory editing, feedback, context, and tags.

These endpoints provide Cloud-parity features for the OSS deployment:
- ``/memory/edit`` — bulk-edit a user's profile items
- ``/memory/update`` — edit one episode's narrative/summary/subject
- ``/memory/feedback`` — rate a memory
- ``/memory/context`` — multi-round LLM-driven retrieval (stub)
- ``/memory/tags/*`` — add/remove tags on memories
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .ids import AppScopeId, PathSafeId, ProjectScopeId


class ProfileEditData(BaseModel):
    """Data for adding or updating a profile item."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    evidence: str | None = Field(default=None, max_length=2000)


class ProfileEditOperation(BaseModel):
    """One operation in a profile edit batch."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["add", "update", "delete"]
    type: Literal["explicit_info", "implicit_traits"]
    data: ProfileEditData | None = Field(
        default=None,
        description="Required for add/update, ignored for delete",
    )
    item_id: str | None = Field(
        default=None,
        max_length=64,
        description="Required for update/delete, ignored for add",
    )
    reason: str | None = Field(default=None, max_length=256)


class ProfileEditRequest(BaseModel):
    """Bulk-edit a single user's profile (Cloud-parity endpoint)."""

    model_config = ConfigDict(extra="forbid")

    user_id: PathSafeId = Field(min_length=1, max_length=128)
    operations: list[ProfileEditOperation] = Field(min_length=1, max_length=50)
    app_id: AppScopeId = Field(default="default", min_length=1, max_length=128)
    project_id: ProjectScopeId = Field(default="default", min_length=1, max_length=128)


class ProfileEditResult(BaseModel):
    """Result of a single profile edit operation."""

    op_index: int
    status: Literal["applied", "rejected"]
    item_id: str | None = None
    message: str | None = None


class ProfileEditResponse(BaseModel):
    """Response for profile edit."""

    user_id: str
    version: int
    applied: int
    results: list[ProfileEditResult]


class MemoryPatch(BaseModel):
    """Fields to update on an episode."""

    model_config = ConfigDict(extra="forbid")

    episode: str | None = Field(default=None, min_length=1, max_length=8000)
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    subject: str | None = Field(default=None, min_length=1, max_length=200)


class MemoryUpdateReason(BaseModel):
    """Audit trail for memory update."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "hallucination",
        "wrong_subject",
        "wrong_time",
        "redundant",
        "missing",
        "privacy",
        "style",
        "other",
    ]
    note: str | None = Field(default=None, max_length=256)


class MemoryUpdateRequest(BaseModel):
    """Edit one episode's narrative, summary, or subject."""

    model_config = ConfigDict(extra="forbid")

    memory_type: Literal["episode"]
    memory_id: str = Field(min_length=1, max_length=256)
    patch: MemoryPatch
    reason: MemoryUpdateReason | None = None
    user_id: PathSafeId | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Owner guard: reject if memory belongs to another user",
    )
    app_id: AppScopeId = Field(default="default", min_length=1, max_length=128)
    project_id: ProjectScopeId = Field(default="default", min_length=1, max_length=128)


class MemoryUpdateResponse(BaseModel):
    """The edited episode, in the same shape as get returns."""

    id: str
    memory_type: str = "episode"
    unchanged: bool = False
    edited_at: str | None = None


class FeedbackRequest(BaseModel):
    """Record that a memory was right or wrong."""

    model_config = ConfigDict(extra="forbid")

    memory_type: Literal["episode", "profile", "agent_case", "agent_skill"]
    memory_id: str = Field(min_length=1, max_length=256)
    item_id: str | None = Field(
        default=None,
        max_length=64,
        description="For profile: the item_id of the rated entry (ei_*/it_*)",
    )
    rating: Literal["positive", "negative"]
    reason: (
        Literal[
            "hallucination",
            "outdated",
            "wrong_subject",
            "wrong_time",
            "redundant",
            "privacy",
            "style",
            "other",
        ]
        | None
    ) = Field(default=None, description="Negative only")
    note: str | None = Field(default=None, min_length=1, max_length=256)
    suggestion: str | None = Field(
        default=None,
        min_length=1,
        max_length=8000,
        description="Negative only: what the memory should have said",
    )
    app_id: AppScopeId = Field(default="default", min_length=1, max_length=128)
    project_id: ProjectScopeId = Field(default="default", min_length=1, max_length=128)


class FeedbackResponse(BaseModel):
    """Feedback record ID."""

    id: int


class FeedbackItem(BaseModel):
    """A single feedback record."""

    id: int
    memory_id: str
    rating: int
    comment: str | None = None
    created_at: str


class FeedbackListResponse(BaseModel):
    """List of feedback for a memory."""

    items: list[FeedbackItem]


class ContextMemory(BaseModel):
    """A memory in the context response."""

    id: str
    kind: str
    score: float
    text: str


class ContextRequest(BaseModel):
    """Multi-round LLM-driven retrieval request."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    user_id: PathSafeId = Field(min_length=1, max_length=128)
    top_k: Annotated[int, Field(ge=1, le=100)] = 10
    app_id: AppScopeId = Field(default="default", min_length=1, max_length=128)
    project_id: ProjectScopeId = Field(default="default", min_length=1, max_length=128)


class ContextResponse(BaseModel):
    """Assembled prompt-ready context string."""

    context: str
    memories: list[ContextMemory]
    tokens_estimate: int


class TagsAddRequest(BaseModel):
    """Add tags to a memory."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=256)
    tags: list[str] = Field(min_length=1, max_length=100)
    app_id: AppScopeId = Field(default="default", min_length=1, max_length=128)
    project_id: ProjectScopeId = Field(default="default", min_length=1, max_length=128)


class TagsRemoveRequest(BaseModel):
    """Remove tags from a memory."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=256)
    tags: list[str] = Field(min_length=1, max_length=100)
    app_id: AppScopeId = Field(default="default", min_length=1, max_length=128)
    project_id: ProjectScopeId = Field(default="default", min_length=1, max_length=128)


class TagsResponse(BaseModel):
    """Result of tag add/remove."""

    memory_id: str
    count: int


class TagsListResponse(BaseModel):
    """List of tags for a memory or memory ids for a tag."""

    tags: list[str] | None = None
    memory_ids: list[str] | None = None
