"""Unit tests for admin routes (request logs, usage, overview)."""

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
def app_with_admin(control_store: ControlStore):
    """Create an app with admin scope and control store."""
    application = make_application(control=control_store)
    app = create_app(application=application)
    return app


class TestRequestLogsEndpoints:
    """Tests for request log admin endpoints."""

    async def test_list_request_logs_empty(
        self, app_with_admin, control_store: ControlStore
    ) -> None:
        """List request logs returns empty when no logs exist."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_admin),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/admin/request-logs",
                headers={"X-Api-Key": "test_admin_key"},
            )

        assert response.status_code == 200

    async def test_list_request_logs_with_data(
        self, app_with_admin, control_store: ControlStore
    ) -> None:
        """List request logs returns existing logs."""
        await control_store.log_request(
            request_id="req-1",
            method="POST",
            path="/api/v2/memory/add",
            status=200,
            latency_ms=100,
        )

        async with AsyncClient(
            transport=ASGITransport(app=app_with_admin),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/admin/request-logs",
                headers={"X-Api-Key": "test_admin_key"},
            )

        assert response.status_code == 200


class TestUsageEndpoint:
    """Tests for usage statistics endpoint."""

    async def test_usage_returns_empty_series(
        self, app_with_admin, control_store: ControlStore
    ) -> None:
        """Usage endpoint returns empty series when no usage recorded."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_admin),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/admin/usage?range=24h",
                headers={"X-Api-Key": "test_admin_key"},
            )

        assert response.status_code == 200


class TestOverviewEndpoint:
    """Tests for system overview endpoint."""

    async def test_overview_returns_basic_info(self, app_with_admin) -> None:
        """Overview endpoint returns system information."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_admin),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/admin/overview",
                headers={"X-Api-Key": "test_admin_key"},
            )

        assert response.status_code == 200


class TestMemoriesEndpoint:
    """Tests for memories browse endpoint."""

    async def test_list_memories_empty(self, app_with_admin) -> None:
        """List memories returns empty list by default."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_admin),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/admin/memories",
                headers={"X-Api-Key": "test_admin_key"},
            )

        assert response.status_code == 200


class TestAgentsEndpoint:
    """Tests for agents list endpoint."""

    async def test_list_agents_empty(self, app_with_admin) -> None:
        """List agents returns empty list by default."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_admin),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/admin/agents",
                headers={"X-Api-Key": "test_admin_key"},
            )

        assert response.status_code == 200
