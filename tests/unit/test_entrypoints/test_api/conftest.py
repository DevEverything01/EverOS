"""Fixtures for API tests that run with authentication enforced.

The root conftest opens the API (``legacy_open``) for the bulk of the suite;
the security tests here need the real auth middleware, a real ``control.db``
and keys with specific scopes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from everos.config import SqliteSettings, load_settings
from everos.entrypoints.api.app import create_app
from everos.infra.persistence.sqlite import ControlStore
from tests.fakes import make_application


@dataclass
class SecuredApp:
    """An auth-enforcing app over a real control store, plus keys."""

    app: FastAPI
    store: ControlStore
    root: Path
    read_key: str
    write_key: str
    admin_key: str
    admin_key_id: str

    def client(self, **kwargs: Any) -> AsyncClient:
        kwargs.setdefault("base_url", "http://test")
        return AsyncClient(transport=ASGITransport(app=self.app), **kwargs)


@pytest.fixture
async def control_store_db(tmp_path: Path) -> AsyncIterator[ControlStore]:
    store = ControlStore(tmp_path / "control.db", SqliteSettings())
    await store.create_schema()
    yield store
    await store.dispose()


@pytest.fixture
def enforce_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the suite-wide ``legacy_open`` so the auth middleware enforces."""
    monkeypatch.setenv("EVEROS_AUTH__LEGACY_OPEN", "false")
    monkeypatch.setenv("EVEROS_AUTH__ENABLED", "true")
    load_settings.cache_clear()


@pytest.fixture
def build_secured_app(
    enforce_auth: None, control_store_db: ControlStore, tmp_path: Path
) -> Callable[..., Any]:
    """Factory: ``await build(memory=..., **create_app_kwargs)``."""

    async def build(*, memory: Any = None, **app_kwargs: Any) -> SecuredApp:
        load_settings.cache_clear()  # pick up env set by the test itself
        application = make_application(
            root=tmp_path, control=control_store_db, memory=memory
        )
        app = create_app(application=application, **app_kwargs)
        read = await control_store_db.create_key("reader", ["memory:read"])
        write = await control_store_db.create_key(
            "writer", ["memory:read", "memory:write"]
        )
        admin = await control_store_db.create_key("admin", ["admin"])
        return SecuredApp(
            app=app,
            store=control_store_db,
            root=tmp_path,
            read_key=read.secret,
            write_key=write.secret,
            admin_key=admin.secret,
            admin_key_id=admin.key_id,
        )

    return build


@pytest.fixture
async def secured(build_secured_app: Callable[..., Any]) -> SecuredApp:
    return await build_secured_app()
