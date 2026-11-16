"""Integration test for console and auth flow (cloud-parity features).

Covers the end-to-end flow:
1. Console assets served
2. Create API key (in legacy_open mode)
3. Add memory
4. Search memory
5. Edit profile
6. Add/remove tags
7. Rotate key
8. View request logs
9. View usage
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from everos.config import SqliteSettings
from everos.entrypoints.api.app import create_app
from everos.infra.persistence.sqlite import ControlStore
from tests.fakes import make_application


@pytest.fixture
async def control_store(tmp_path: Path) -> ControlStore:
    """Create a temporary ControlStore for testing."""
    db_path = tmp_path / "control.db"
    settings = SqliteSettings()
    store = ControlStore(db_path, settings)
    await store.create_schema()
    yield store
    await store.dispose()


@pytest.fixture
def app(control_store: ControlStore):
    """Create an app with control store in legacy_open mode."""
    os.environ["EVEROS_AUTH__LEGACY_OPEN"] = "true"
    application = make_application(control=control_store)
    app = create_app(application=application)
    yield app
    os.environ.pop("EVEROS_AUTH__LEGACY_OPEN", None)


class TestConsoleAuthFlow:
    """Integration test for console and auth flow."""

    async def test_console_html_served(self, app) -> None:
        """Console HTML is served at /console."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "<!doctype html>" in response.text.lower()

    async def test_console_js_served(self, app) -> None:
        """Console JS is served at /console/app.js."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/app.js")

        assert response.status_code == 200
        assert "javascript" in response.headers.get("content-type", "")

    async def test_console_css_served(self, app) -> None:
        """Console CSS is served at /console/app.css."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/app.css")

        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")

    async def test_create_api_key(self, app, control_store: ControlStore) -> None:
        """Create API key returns key with secret."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/auth/keys",
                json={"name": "test-key", "scopes": ["admin"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert "key_id" in data
        assert "key" in data
        assert data["key"].startswith("eos_")
        assert data["name"] == "test-key"
        assert "admin" in data["scopes"]

    async def test_list_api_keys(self, app, control_store: ControlStore) -> None:
        """List API keys returns keys without secrets."""
        await control_store.create_key("test-key", scopes=["admin"])

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v2/auth/keys")

        assert response.status_code == 200
        data = response.json()
        assert "keys" in data
        assert len(data["keys"]) >= 1
        assert "key" not in data["keys"][0]

    async def test_rotate_api_key(self, app, control_store: ControlStore) -> None:
        """Rotate API key returns new secret."""
        key = await control_store.create_key("to-rotate", scopes=["admin"])
        old_secret = key.secret

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(f"/api/v2/auth/keys/{key.key_id}/rotate")

        assert response.status_code == 200
        data = response.json()
        assert data["key"] != old_secret
        assert data["key"].startswith("eos_")

    async def test_revoke_api_key(self, app, control_store: ControlStore) -> None:
        """Revoke API key marks it as revoked."""
        key = await control_store.create_key("to-revoke", scopes=["admin"])

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.delete(f"/api/v2/auth/keys/{key.key_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "revoked"

        found = await control_store.lookup_key(key.secret)
        assert found is None

    async def test_add_feedback(self, app, control_store: ControlStore) -> None:
        """Add feedback stores it in control store."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/feedback",
                json={
                    "memory_type": "episode",
                    "memory_id": "test_ep_001",
                    "rating": "positive",
                },
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "id" in data

    async def test_add_and_remove_tags(self, app, control_store: ControlStore) -> None:
        """Add and remove tags on a memory."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            add_response = await client.post(
                "/api/v2/memory/tags/add",
                json={"memory_id": "test_ep_001", "tags": ["important", "work"]},
            )
            assert add_response.status_code == 200

            tags = await control_store.get_tags("test_ep_001")
            assert set(tags) == {"important", "work"}

            remove_response = await client.post(
                "/api/v2/memory/tags/remove",
                json={"memory_id": "test_ep_001", "tags": ["work"]},
            )
            assert remove_response.status_code == 200

            tags = await control_store.get_tags("test_ep_001")
            assert set(tags) == {"important"}

    async def test_request_logs_recorded(
        self, app, control_store: ControlStore
    ) -> None:
        """Request logs are recorded in control store."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            await client.get("/api/v2/admin/overview")

            response = await client.get("/api/v2/admin/request-logs?limit=10")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "items" in data
        assert "total" in data

    async def test_usage_endpoint(self, app, control_store: ControlStore) -> None:
        """Usage endpoint returns statistics."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v2/admin/usage?range=24h")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "series" in data
        assert "totals" in data

    async def test_overview_endpoint(self, app, control_store: ControlStore) -> None:
        """Overview endpoint returns system info."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v2/admin/overview")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "version" in data
        assert "auth_mode" in data
        assert "counts" in data

    async def test_edit_profile(self, app) -> None:
        """Edit profile endpoint accepts operations."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/edit",
                json={
                    "user_id": "test_user",
                    "operations": [
                        {
                            "action": "add",
                            "type": "explicit_info",
                            "data": {
                                "category": "preference",
                                "description": "Likes coffee",
                            },
                        }
                    ],
                },
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["user_id"] == "test_user"
        assert data["applied"] == 1

    async def test_update_memory(self, app) -> None:
        """Update memory endpoint accepts patch."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/update",
                json={
                    "memory_type": "episode",
                    "memory_id": "test_ep_001",
                    "patch": {"summary": "Updated summary"},
                },
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == "test_ep_001"
        assert data["memory_type"] == "episode"

    async def test_context_endpoint(self, app) -> None:
        """Context endpoint returns context string."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/context",
                json={
                    "query": "What does the user like?",
                    "user_id": "test_user",
                    "top_k": 5,
                },
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "context" in data
        assert "memories" in data
        assert "tokens_estimate" in data
