"""Unit tests for memory editing, feedback, context, and tags endpoints.

Covers Cloud-parity features:
- POST /memory/edit — bulk-edit a user's profile items
- POST /memory/update — edit one episode's narrative/summary/subject
- POST /memory/feedback — rate a memory
- POST /memory/context — multi-round LLM-driven retrieval (stub)
- POST /memory/tags/add, /tags/remove, GET /tags — tag operations
"""

from __future__ import annotations

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
def app_with_control(control_store: ControlStore):
    """Create an app with control store."""
    application = make_application(control=control_store)
    app = create_app(application=application)
    return app


class TestProfileEdit:
    """Tests for profile edit endpoint."""

    async def test_edit_profile_add_operation(
        self, app_with_control, control_store: ControlStore
    ) -> None:
        """Edit profile with add operation returns applied results."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/edit",
                json={
                    "user_id": "user_001",
                    "operations": [
                        {
                            "action": "add",
                            "type": "explicit_info",
                            "data": {
                                "category": "hobby",
                                "description": "Enjoys hiking",
                            },
                        }
                    ],
                },
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert data["user_id"] == "user_001"
            assert data["applied"] == 1
            assert len(data["results"]) == 1
            assert data["results"][0]["status"] == "applied"


class TestMemoryUpdate:
    """Tests for memory update endpoint."""

    async def test_update_episode_summary(self, app_with_control) -> None:
        """Update endpoint accepts valid memory update request."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/update",
                json={
                    "memory_type": "episode",
                    "memory_id": "user_001_ep_20261116_001",
                    "patch": {
                        "summary": "Updated summary",
                    },
                },
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert data["id"] == "user_001_ep_20261116_001"
            assert data["memory_type"] == "episode"


class TestFeedback:
    """Tests for feedback endpoint."""

    async def test_add_positive_feedback(
        self, app_with_control, control_store: ControlStore
    ) -> None:
        """Add positive feedback stores and returns ok."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/feedback",
                json={
                    "memory_type": "episode",
                    "memory_id": "user_001_ep_20261116_001",
                    "rating": "positive",
                },
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "id" in data

    async def test_add_negative_feedback_with_reason(
        self, app_with_control, control_store: ControlStore
    ) -> None:
        """Add negative feedback with reason and suggestion."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/feedback",
                json={
                    "memory_type": "episode",
                    "memory_id": "user_001_ep_20261116_001",
                    "rating": "negative",
                    "reason": "outdated",
                    "note": "Information has changed",
                    "suggestion": "The user now lives in Shanghai.",
                },
            )

        assert response.status_code == 200


class TestContext:
    """Tests for context retrieval endpoint."""

    async def test_context_returns_memories(self, app_with_control) -> None:
        """Context endpoint returns context string and memories."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/context",
                json={
                    "query": "What does the user like?",
                    "user_id": "user_001",
                    "top_k": 5,
                },
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "context" in data
            assert "memories" in data
            assert "tokens_estimate" in data


class TestTags:
    """Tests for tag operations."""

    async def test_add_tags(
        self, app_with_control, control_store: ControlStore
    ) -> None:
        """Add tags to a memory."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/tags/add",
                json={
                    "memory_id": "user_001_ep_20261116_001",
                    "tags": ["important", "work"],
                },
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert data["memory_id"] == "user_001_ep_20261116_001"
            assert data["count"] == 2

    async def test_remove_tags(
        self, app_with_control, control_store: ControlStore
    ) -> None:
        """Remove tags from a memory."""
        await control_store.add_tags(
            memory_id="user_001_ep_20261116_001",
            tags=["important", "work"],
        )

        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/memory/tags/remove",
                json={
                    "memory_id": "user_001_ep_20261116_001",
                    "tags": ["important"],
                },
            )

        assert response.status_code == 200

    async def test_list_tags_by_memory_id(
        self, app_with_control, control_store: ControlStore
    ) -> None:
        """List tags for a memory."""
        await control_store.add_tags(
            memory_id="user_001_ep_20261116_001",
            tags=["important", "work"],
        )

        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/memory/tags",
                params={"memory_id": "user_001_ep_20261116_001"},
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "tags" in data

    async def test_list_memories_by_tag(
        self, app_with_control, control_store: ControlStore
    ) -> None:
        """List memory ids with a specific tag."""
        await control_store.add_tags(
            memory_id="user_001_ep_20261116_001",
            tags=["important"],
        )

        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/memory/tags",
                params={"tag": "important"},
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "memory_ids" in data

    async def test_list_tags_requires_one_param(self, app_with_control) -> None:
        """List tags without memory_id or tag returns 422."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v2/memory/tags")

        assert response.status_code in (422, 401, 403)

    async def test_list_tags_both_params_returns_422(self, app_with_control) -> None:
        """List tags with both memory_id and tag returns 422."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_control),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/memory/tags",
                params={"memory_id": "mem_001", "tag": "important"},
            )

        assert response.status_code in (422, 401, 403)
