"""CORS never pairs credentials with ``*``; API docs are off outside ``ENV=DEV``.

Before: the default was ``allow_origins=["*"]`` with ``allow_credentials=True``
(Starlette then echoes any Origin and allows credentials, so any site could
read API responses with the console cookie), and ``/docs`` +
``/openapi.json`` were public by default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from everos.config import load_settings
from everos.config.settings import ServerSettings
from everos.entrypoints.api.app import create_app
from everos.entrypoints.api.middleware import (
    DEFAULT_CORS_ALLOW_CREDENTIALS,
    effective_allow_credentials,
)
from everos.infra.persistence.sqlite import ControlStore
from tests.fakes import make_application

EVIL = "https://evil.example"
GOOD = "https://console.example"


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _preflight(app, origin: str):
    async with _client(app) as client:
        return await client.options(
            "/api/v2/memory/search",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )


class TestCors:
    def test_default_disallows_credentials(self) -> None:
        assert DEFAULT_CORS_ALLOW_CREDENTIALS is False

    def test_wildcard_forces_credentials_off(self) -> None:
        assert effective_allow_credentials(["*"], True) is False
        assert effective_allow_credentials([GOOD, "*"], True) is False
        assert effective_allow_credentials([GOOD], True) is True

    async def test_default_preflight_has_no_credentials(self, tmp_path: Path) -> None:
        app = create_app(application=make_application(root=tmp_path))

        response = await _preflight(app, EVIL)

        assert response.status_code == 200
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers["access-control-allow-origin"] == "*"

    async def test_wildcard_with_credentials_true_is_overridden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        warnings: list[str] = []
        monkeypatch.setattr(
            "everos.entrypoints.api.app.logger.warning",
            lambda event, **_: warnings.append(event),
        )
        app = create_app(
            application=make_application(root=tmp_path),
            cors_origins=["*"],
            cors_allow_credentials=True,
        )

        response = await _preflight(app, EVIL)

        assert "access-control-allow-credentials" not in response.headers
        assert response.headers["access-control-allow-origin"] == "*"
        assert "cors_credentials_disabled" in warnings

    async def test_explicit_allowlist_with_credentials(self, tmp_path: Path) -> None:
        app = create_app(
            application=make_application(root=tmp_path),
            cors_origins=[GOOD],
            cors_allow_credentials=True,
        )

        allowed = await _preflight(app, GOOD)
        refused = await _preflight(app, EVIL)

        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-credentials"] == "true"
        assert allowed.headers["access-control-allow-origin"] == GOOD
        assert refused.status_code == 400
        assert "access-control-allow-origin" not in refused.headers


class TestDocsDefault:
    def test_off_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENV", raising=False)

        assert ServerSettings().docs_enabled is False

    def test_on_in_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "dev")

        assert ServerSettings().docs_enabled is True

    def test_explicit_true_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENV", raising=False)

        assert ServerSettings(docs_enabled=True).docs_enabled is True

    async def test_production_app_hides_docs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        control_store_db: ControlStore,
    ) -> None:
        monkeypatch.delenv("ENV", raising=False)
        load_settings.cache_clear()
        app = create_app(
            application=make_application(root=tmp_path, control=control_store_db)
        )

        async with _client(app) as client:
            docs = await client.get("/docs")
            schema = await client.get("/openapi.json")
            console = await client.get("/console/")
            overview = await client.get("/api/v2/admin/overview")

        assert docs.status_code == 404
        assert schema.status_code == 404
        # Hiding the API docs must not take the console down with them, and
        # the console is told so it can hide its links to /docs.
        assert console.status_code == 200
        assert "text/html" in console.headers["content-type"]
        assert overview.status_code == 200
        assert overview.json()["data"]["docs_enabled"] is False

    async def test_dev_app_serves_docs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        control_store_db: ControlStore,
    ) -> None:
        monkeypatch.setenv("ENV", "DEV")
        load_settings.cache_clear()
        app = create_app(
            application=make_application(root=tmp_path, control=control_store_db)
        )

        async with _client(app) as client:
            schema = await client.get("/openapi.json")
            overview = await client.get("/api/v2/admin/overview")

        assert schema.status_code == 200
        assert overview.json()["data"]["docs_enabled"] is True
