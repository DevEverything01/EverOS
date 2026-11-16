"""Unit tests for the authentication middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from starlette.responses import Response

from everos.config import AuthSettings
from everos.entrypoints.api.middleware.auth import (
    AuthMiddleware,
    _extract_token,
    _is_exempt,
    require_any_scope,
    require_scope,
)


class TestExtractToken:
    """Tests for token extraction from headers."""

    def test_bearer_token(self) -> None:
        """Extracts token from Authorization: Bearer header."""
        request = MagicMock()
        request.headers = {"Authorization": "Bearer eos_abc123_secret"}

        token = _extract_token(request)

        assert token == "eos_abc123_secret"

    def test_x_api_key_header(self) -> None:
        """Extracts token from X-Api-Key header."""
        request = MagicMock()
        request.headers = {"X-Api-Key": "eos_abc123_secret"}

        token = _extract_token(request)

        assert token == "eos_abc123_secret"

    def test_bearer_preferred_over_x_api_key(self) -> None:
        """Bearer header takes precedence over X-Api-Key."""
        request = MagicMock()
        request.headers = {
            "Authorization": "Bearer bearer_token",
            "X-Api-Key": "api_key_token",
        }

        token = _extract_token(request)

        assert token == "bearer_token"

    def test_no_token(self) -> None:
        """Returns None when no token header is present."""
        request = MagicMock()
        request.headers = {}

        token = _extract_token(request)

        assert token is None

    def test_non_bearer_auth(self) -> None:
        """Returns None for non-Bearer Authorization."""
        request = MagicMock()
        request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}

        token = _extract_token(request)

        assert token is None


class TestIsExempt:
    """Tests for path exemption checking."""

    def test_exact_match(self) -> None:
        """Exact path match is exempt."""
        assert _is_exempt("/health", ["/health"]) is True

    def test_prefix_match(self) -> None:
        """Prefix match is exempt."""
        assert _is_exempt("/console/index.html", ["/console"]) is True
        assert _is_exempt("/console/", ["/console"]) is True

    def test_no_match(self) -> None:
        """Non-matching path is not exempt."""
        assert _is_exempt("/api/v2/memory/add", ["/health", "/console"]) is False

    def test_partial_match_not_exempt(self) -> None:
        """Partial match (not prefix) is not exempt."""
        assert _is_exempt("/healthcheck", ["/health"]) is False


class TestRequireScope:
    """Tests for scope checking helpers."""

    def test_exact_scope_match(self) -> None:
        """Exact scope match returns True."""
        assert require_scope(["memory:read", "memory:write"], "memory:read") is True

    def test_admin_grants_all(self) -> None:
        """Admin scope grants any required scope."""
        assert require_scope(["admin"], "memory:write") is True

    def test_missing_scope(self) -> None:
        """Missing scope returns False."""
        assert require_scope(["memory:read"], "memory:write") is False

    def test_any_scope_match(self) -> None:
        """require_any_scope matches any of the required scopes."""
        assert require_any_scope(["memory:read"], ["memory:read", "memory:write"])

    def test_any_scope_admin(self) -> None:
        """Admin scope satisfies require_any_scope."""
        assert require_any_scope(["admin"], ["memory:read", "memory:write"])

    def test_any_scope_none(self) -> None:
        """require_any_scope returns False when no scopes match."""
        assert require_any_scope(["other"], ["memory:read", "memory:write"]) is False


class TestAuthMiddleware:
    """Tests for AuthMiddleware behavior."""

    def _make_middleware(
        self,
        *,
        enabled: bool = True,
        legacy_open: bool = False,
        exempt_paths: list[str] | None = None,
    ) -> AuthMiddleware:
        """Create middleware with test settings."""
        settings = AuthSettings(
            enabled=enabled,
            legacy_open=legacy_open,
            exempt_paths=exempt_paths or ["/health"],
        )
        return AuthMiddleware(MagicMock(), settings)

    async def test_auth_disabled_passes_through(self) -> None:
        """When auth is disabled, requests pass through."""
        middleware = self._make_middleware(enabled=False)
        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        call_next.assert_called_once()

    async def test_legacy_open_passes_through(self) -> None:
        """When legacy_open is True, requests pass through."""
        middleware = self._make_middleware(enabled=True, legacy_open=True)
        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        call_next.assert_called_once()

    async def test_exempt_path_passes_through(self) -> None:
        """Exempt paths pass through without auth."""
        middleware = self._make_middleware(exempt_paths=["/health"])
        request = MagicMock()
        request.url.path = "/health"
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        call_next.assert_called_once()

    async def test_non_api_path_passes_through(self) -> None:
        """Non-API paths pass through without auth."""
        middleware = self._make_middleware()
        request = MagicMock()
        request.url.path = "/some/other/path"
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        call_next.assert_called_once()

    async def test_missing_token_returns_401(self) -> None:
        """Missing token returns 401."""
        middleware = self._make_middleware()
        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.headers = {}
        request.app.state.control_store = MagicMock()
        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()

    async def test_invalid_token_returns_401(self) -> None:
        """Invalid token returns 401."""
        middleware = self._make_middleware()
        store = AsyncMock()
        store.lookup_key.return_value = None

        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.headers = {"Authorization": "Bearer invalid_token"}
        request.app.state.control_store = store
        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()

    async def test_valid_token_sets_state(self) -> None:
        """Valid token sets request.state fields."""
        middleware = self._make_middleware()

        api_key = MagicMock()
        api_key.key_id = "key_123"
        api_key.scopes = '["memory:read", "memory:write"]'

        store = AsyncMock()
        store.lookup_key.return_value = api_key

        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.headers = {"Authorization": "Bearer valid_token"}
        request.app.state.control_store = store
        request.state = MagicMock()
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        assert request.state.api_key_id == "key_123"
        assert request.state.scopes == ["memory:read", "memory:write"]
        call_next.assert_called_once()
        store.touch_key.assert_called_once_with("key_123")

    async def test_bootstrap_token_creates_key_on_first_use(self) -> None:
        """Bootstrap token creates admin key on first use when no keys exist."""
        from pydantic import SecretStr

        settings = AuthSettings(
            enabled=True,
            legacy_open=False,
            exempt_paths=["/health"],
            bootstrap_token=SecretStr("test-bootstrap-token"),
        )
        middleware = AuthMiddleware(MagicMock(), settings)

        created_key = MagicMock()
        created_key.key_id = "key_bootstrap"
        created_key.prefix = "abcd1234"

        store = AsyncMock()
        store.key_count.return_value = 0
        store.create_key.return_value = created_key
        store.lookup_key.return_value = None

        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.headers = {"Authorization": "Bearer test-bootstrap-token"}
        request.app.state.control_store = store
        request.state = MagicMock()
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        store.create_key.assert_called_once_with(
            "bootstrap-admin",
            scopes=["memory:read", "memory:write", "admin"],
        )
        assert request.state.api_key_id == "bootstrap"
        assert request.state.scopes == ["memory:read", "memory:write", "admin"]
        call_next.assert_called_once()

    async def test_bootstrap_token_rejected_when_keys_exist(self) -> None:
        """Bootstrap token is rejected when API keys already exist."""
        from pydantic import SecretStr

        settings = AuthSettings(
            enabled=True,
            legacy_open=False,
            exempt_paths=["/health"],
            bootstrap_token=SecretStr("test-bootstrap-token"),
        )
        middleware = AuthMiddleware(MagicMock(), settings)

        store = AsyncMock()
        store.key_count.return_value = 1
        store.lookup_key.return_value = None

        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.headers = {"Authorization": "Bearer test-bootstrap-token"}
        request.app.state.control_store = store
        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 401
        store.create_key.assert_not_called()
        call_next.assert_not_called()

    async def test_wrong_bootstrap_token_rejected(self) -> None:
        """Wrong bootstrap token is rejected."""
        from pydantic import SecretStr

        settings = AuthSettings(
            enabled=True,
            legacy_open=False,
            exempt_paths=["/health"],
            bootstrap_token=SecretStr("correct-token"),
        )
        middleware = AuthMiddleware(MagicMock(), settings)

        store = AsyncMock()
        store.key_count.return_value = 0
        store.lookup_key.return_value = None

        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.headers = {"Authorization": "Bearer wrong-token"}
        request.app.state.control_store = store
        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 401
        store.create_key.assert_not_called()
        call_next.assert_not_called()
