"""Scope enforcement: admin routes need ``admin``, write routes ``memory:write``.

``_require_admin`` used to be an empty function, so any valid key -- even a
``memory:read`` one -- could mint admin keys, revoke others and read the
request logs.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import SecuredApp

EP = "user_123_ep_20261116_00000001"

# (method, path, json body); {key} is substituted with a throwaway key id.
ADMIN_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v2/admin/request-logs", None),
    ("GET", "/api/v2/admin/request-logs/1", None),
    ("GET", "/api/v2/admin/usage", None),
    ("GET", "/api/v2/admin/overview", None),
    ("GET", "/api/v2/admin/memories", None),
    ("GET", "/api/v2/admin/memories/m1", None),
    ("GET", "/api/v2/admin/agents", None),
    ("GET", "/api/v2/auth/keys", None),
    ("POST", "/api/v2/auth/keys", {"name": "minted", "scopes": ["admin"]}),
    ("POST", "/api/v2/auth/keys/{key}/rotate", None),
    ("DELETE", "/api/v2/auth/keys/{key}", None),
]

WRITE_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v2/memory/add", {"user_id": "u1", "messages": []}),
    ("POST", "/api/v2/memory/flush", {"user_id": "u1", "session_id": "s1"}),
    ("POST", "/api/v2/memory/delete", {"user_id": "u1"}),
    (
        "POST",
        "/api/v2/memory/delete_by_ids",
        {"memory_type": "episode", "memory_ids": [EP]},
    ),
    ("POST", "/api/v2/memory/edit", {"user_id": "u1", "operations": []}),
    (
        "POST",
        "/api/v2/memory/update",
        {"memory_type": "episode", "memory_id": EP, "patch": {"summary": "x"}},
    ),
    (
        "POST",
        "/api/v2/memory/feedback",
        {"memory_type": "episode", "memory_id": EP, "rating": "positive"},
    ),
    ("POST", "/api/v2/memory/tags/add", {"memory_id": EP, "tags": ["t"]}),
    ("POST", "/api/v2/memory/tags/remove", {"memory_id": EP, "tags": ["t"]}),
    ("POST", "/api/v2/memory/purge", {"scope": "space"}),
    ("POST", "/api/v2/memory/purge/confirm", {"nonce": "n"}),
    ("DELETE", "/api/v2/knowledge/documents/doc_1", None),
    ("PATCH", "/api/v2/knowledge/documents/doc_1", {"title": "t"}),
    ("POST", "/api/v2/knowledge_bases", {"name": "kb"}),
    ("DELETE", "/api/v2/knowledge_bases/kb_1", None),
]


async def _call(
    secured: SecuredApp, key: str, method: str, path: str, body: Any
) -> int:
    if "{key}" in path:
        throwaway = await secured.store.create_key("throwaway", ["memory:read"])
        path = path.replace("{key}", throwaway.key_id)
    async with secured.client(headers={"X-Api-Key": key}) as client:
        response = await client.request(method, path, json=body)
    return response.status_code


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
async def test_read_key_is_forbidden_on_admin_routes(
    secured: SecuredApp, method: str, path: str, body: Any
) -> None:
    assert await _call(secured, secured.read_key, method, path, body) == 403


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
async def test_write_key_is_forbidden_on_admin_routes(
    secured: SecuredApp, method: str, path: str, body: Any
) -> None:
    assert await _call(secured, secured.write_key, method, path, body) == 403


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
async def test_admin_key_is_allowed_on_admin_routes(
    secured: SecuredApp, method: str, path: str, body: Any
) -> None:
    await secured.store.log_request(
        request_id="r1", method="GET", path="/api/v2/x", status=200, latency_ms=1
    )

    assert await _call(secured, secured.admin_key, method, path, body) == 200


@pytest.mark.parametrize(("method", "path", "body"), WRITE_ROUTES)
async def test_read_key_is_forbidden_on_write_routes(
    secured: SecuredApp, method: str, path: str, body: Any
) -> None:
    assert await _call(secured, secured.read_key, method, path, body) == 403


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v2/memory/tags/add", {"memory_id": EP, "tags": ["t"]}),
        (
            "/api/v2/memory/feedback",
            {"memory_type": "episode", "memory_id": EP, "rating": "positive"},
        ),
    ],
)
@pytest.mark.parametrize("key_attr", ["write_key", "admin_key"])
async def test_write_and_admin_keys_may_write(
    secured: SecuredApp, key_attr: str, path: str, body: Any
) -> None:
    assert await _call(secured, getattr(secured, key_attr), "POST", path, body) == 200


async def test_read_key_still_reads(secured: SecuredApp) -> None:
    status = await _call(
        secured, secured.read_key, "GET", f"/api/v2/memory/tags?memory_id={EP}", None
    )

    assert status == 200


async def test_read_key_cannot_mint_an_admin_key(secured: SecuredApp) -> None:
    before = len(await secured.store.list_keys())

    status = await _call(
        secured,
        secured.read_key,
        "POST",
        "/api/v2/auth/keys",
        {"name": "escalate", "scopes": ["admin"]},
    )

    assert status == 403
    assert len(await secured.store.list_keys()) == before


async def test_forbidden_envelope(secured: SecuredApp) -> None:
    async with secured.client(headers={"X-Api-Key": secured.read_key}) as client:
        response = await client.get("/api/v2/admin/request-logs")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_open_mode_keeps_admin_routes_reachable(
    control_store_db, tmp_path
) -> None:
    """``legacy_open`` (suite default) is an explicitly open deployment."""
    from httpx import ASGITransport, AsyncClient

    from everos.entrypoints.api.app import create_app
    from tests.fakes import make_application

    app = create_app(
        application=make_application(root=tmp_path, control=control_store_db)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v2/admin/request-logs")

    assert response.status_code == 200
