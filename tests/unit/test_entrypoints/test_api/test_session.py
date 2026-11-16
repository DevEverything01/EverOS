"""Tests for console session cookie signing and middleware authentication."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from everos.config import AuthSettings
from everos.entrypoints.api.middleware.auth import AuthMiddleware, _is_session_path
from everos.entrypoints.api.session import resolve_secret, sign_session, verify_session

SECRET = "test-session-secret"


class TestSessionToken:
    """Signing and verification of console session cookies."""

    def test_roundtrip_returns_key_id(self) -> None:
        value = sign_session("key_abc", SECRET)

        assert verify_session(value, SECRET) == "key_abc"

    def test_wrong_secret_is_rejected(self) -> None:
        value = sign_session("key_abc", SECRET)

        assert verify_session(value, "other-secret") is None

    def test_tampered_payload_is_rejected(self) -> None:
        value = sign_session("key_abc", SECRET)
        payload, signature = value.rsplit(":", 1)

        assert (
            verify_session(
                payload.replace("key_abc", "key_zzz") + ":" + signature, SECRET
            )
            is None
        )

    def test_garbage_is_rejected(self) -> None:
        assert verify_session("not-a-session", SECRET) is None


class TestSessionPaths:
    """The session endpoints authenticate from their own body."""

    @pytest.mark.parametrize(
        "path",
        ["/api/v2/auth/session", "/api/v2/auth/session/", "/api/v1/auth/session"],
    )
    def test_session_paths_detected(self, path: str) -> None:
        assert _is_session_path(path) is True

    def test_other_paths_are_not_session_paths(self) -> None:
        assert _is_session_path("/api/v2/auth/keys") is False


class TestCookieAuthentication:
    """A valid cookie authenticates a request without a bearer token."""

    @pytest.mark.asyncio
    async def test_valid_cookie_attaches_key_state(self) -> None:
        middleware = AuthMiddleware(MagicMock(), AuthSettings())
        request = MagicMock()
        request.app.state.application = SimpleNamespace(
            _settings=SimpleNamespace(auth=AuthSettings(session_secret=SECRET))
        )
        request.cookies = {"everos_session": sign_session("key_abc", SECRET)}
        request.app.state.control_store = MagicMock()
        store = AsyncMock()
        store.get_key_by_id.return_value = SimpleNamespace(
            key_id="key_abc", scopes='["admin"]'
        )

        key_id = await middleware._session_key(request, store)

        assert key_id == "key_abc"
        assert request.state.scopes == ["admin"]
        store.touch_key.assert_awaited_once_with("key_abc")

    @pytest.mark.asyncio
    async def test_revoked_key_rejected(self) -> None:
        middleware = AuthMiddleware(MagicMock(), AuthSettings())
        request = MagicMock()
        request.app.state.application = SimpleNamespace(
            _settings=SimpleNamespace(auth=AuthSettings(session_secret=SECRET))
        )
        request.cookies = {"everos_session": sign_session("key_gone", SECRET)}
        store = AsyncMock()
        store.get_key_by_id.return_value = None

        assert await middleware._session_key(request, store) is None

    @pytest.mark.asyncio
    async def test_no_cookie_returns_none(self) -> None:
        middleware = AuthMiddleware(MagicMock(), AuthSettings())
        request = MagicMock()
        request.cookies = {}

        assert await middleware._session_key(request, AsyncMock()) is None


def test_resolve_secret_prefers_settings() -> None:
    request = MagicMock()
    request.app.state.application = SimpleNamespace(
        _settings=SimpleNamespace(auth=AuthSettings(session_secret="configured"))
    )

    assert resolve_secret(request) == "configured"
