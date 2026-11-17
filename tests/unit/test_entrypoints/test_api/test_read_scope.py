"""Read routes need ``memory:read``; ``memory:write`` alone does not imply it.

Before: no read route checked a scope, so a write-only key (``memory:write``)
could ``/memory/search`` and ``/memory/get`` every user's memory, although
``docs/auth.md`` lists those routes under ``memory:read``.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import SecuredApp

# Every read route; the 403 is decided before any request validation.
READ_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v2/memory/search", {"query": "x", "user_id": "u1"}),
    ("POST", "/api/v2/memory/get", {"memory_type": "episode", "user_id": "u1"}),
    ("POST", "/api/v2/memory/context", {"query": "x", "user_id": "u1"}),
    ("GET", "/api/v2/memory/feedback?memory_id=m1", None),
    ("GET", "/api/v2/memory/tags?memory_id=m1", None),
    ("GET", "/api/v2/memory/purge/job_1", None),
    ("GET", "/api/v2/tasks", None),
    ("GET", "/api/v2/tasks/t1", None),
    ("GET", "/api/v2/knowledge/documents", None),
    ("GET", "/api/v2/knowledge/documents/doc_1", None),
    ("GET", "/api/v2/knowledge/topics/t1", None),
    ("GET", "/api/v2/knowledge/categories", None),
    ("POST", "/api/v2/knowledge/search", {"query": "x"}),
    ("GET", "/api/v2/knowledge_bases", None),
    ("GET", "/api/v2/knowledge_bases/kb_1/documents", None),
    ("GET", "/api/v2/knowledge_bases/kb_1/documents/doc_1", None),
    ("GET", "/api/v2/knowledge_bases/kb_1/categories", None),
    ("GET", "/api/v2/knowledge_bases/kb_1/topics/t1", None),
    ("POST", "/api/v2/knowledge_bases/kb_1/search", {"query": "x"}),
]

# Read routes whose stub-backed answer to a well-formed request is 200.
OK_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v2/memory/context", {"query": "x", "user_id": "u1"}),
    ("GET", "/api/v2/memory/feedback?memory_id=m1", None),
    ("GET", "/api/v2/memory/tags?memory_id=m1", None),
    ("GET", "/api/v2/tasks", None),
    ("GET", "/api/v2/knowledge/documents", None),
    ("GET", "/api/v2/knowledge/categories", None),
    ("POST", "/api/v2/knowledge/search", {"query": "x"}),
    ("GET", "/api/v2/knowledge_bases", None),
]


async def _status(secured: SecuredApp, key: str, method: str, path: str, body: Any):
    async with secured.client(headers={"X-Api-Key": key}) as client:
        response = await client.request(method, path, json=body)
    return response.status_code


@pytest.fixture
async def write_only_key(secured: SecuredApp) -> str:
    return (await secured.store.create_key("ingest", ["memory:write"])).secret


@pytest.mark.parametrize(("method", "path", "body"), READ_ROUTES)
async def test_write_only_key_cannot_read(
    secured: SecuredApp, write_only_key: str, method: str, path: str, body: Any
) -> None:
    assert await _status(secured, write_only_key, method, path, body) == 403


@pytest.mark.parametrize("key", ["read_key", "admin_key"])
@pytest.mark.parametrize(("method", "path", "body"), OK_ROUTES)
async def test_read_or_admin_key_reads(
    secured: SecuredApp, key: str, method: str, path: str, body: Any
) -> None:
    assert await _status(secured, getattr(secured, key), method, path, body) == 200
