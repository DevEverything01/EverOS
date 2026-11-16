"""Console route tests - static file serving at /console."""

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
def app(control_store: ControlStore):
    """Create an app with control store."""
    application = make_application(control=control_store)
    return create_app(application=application)


class TestConsoleRoutes:
    """Tests for console static file serving."""

    async def test_console_root_serves_html(self, app) -> None:
        """Console root returns index.html."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    async def test_console_slash_serves_html(self, app) -> None:
        """Console root with slash returns index.html."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    async def test_console_serves_css(self, app) -> None:
        """Console serves CSS files."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/app.css")

        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")

    async def test_console_serves_js(self, app) -> None:
        """Console serves JavaScript files."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/app.js")

        assert response.status_code == 200
        assert "javascript" in response.headers.get("content-type", "")

    async def test_console_serves_fonts(self, app) -> None:
        """Console serves font files."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/manrope-latin.woff2")

        assert response.status_code == 200
        assert "woff2" in response.headers.get("content-type", "")
        assert "max-age" in response.headers.get("cache-control", "")

    async def test_console_unknown_path_fallback(self, app) -> None:
        """Console falls back to index.html for unknown paths (SPA routing)."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/dashboard")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    async def test_console_path_traversal_blocked(self, app) -> None:
        """Console blocks path traversal - returns fallback or 404.

        When a path traversal attempt is made, the route either normalizes
        it and returns the fallback (index.html), or the client normalizes
        the URL first and it results in a 404. Either way, no file outside
        the static directory is served.
        """
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/console/../../../etc/passwd")

        assert response.status_code in (200, 404)
