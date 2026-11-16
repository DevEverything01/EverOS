"""Unit tests for the request logging middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from starlette.responses import Response

from everos.config import RequestLogsSettings
from everos.entrypoints.api.middleware.request_logging import (
    RequestLoggingMiddleware,
    _extract_client_ip,
    _get_metric_for_path,
    _is_exempt,
)


class TestExtractClientIp:
    """Tests for client IP extraction."""

    def test_x_forwarded_for(self) -> None:
        """Extracts first IP from X-Forwarded-For."""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}

        ip = _extract_client_ip(request)

        assert ip == "1.2.3.4"

    def test_direct_client(self) -> None:
        """Falls back to request.client.host."""
        request = MagicMock()
        request.headers = {}
        request.client.host = "10.0.0.1"

        ip = _extract_client_ip(request)

        assert ip == "10.0.0.1"

    def test_no_client(self) -> None:
        """Returns None when no client info available."""
        request = MagicMock()
        request.headers = {}
        request.client = None

        ip = _extract_client_ip(request)

        assert ip is None


class TestIsExempt:
    """Tests for path exemption checking."""

    def test_exact_match(self) -> None:
        """Exact path match is exempt."""
        assert _is_exempt("/health", ["/health"]) is True

    def test_prefix_match(self) -> None:
        """Prefix match is exempt."""
        assert _is_exempt("/docs/", ["/docs"]) is True
        assert _is_exempt("/docs/swagger", ["/docs"]) is True

    def test_no_match(self) -> None:
        """Non-matching path is not exempt."""
        assert _is_exempt("/api/v2/memory/add", ["/health", "/docs"]) is False


class TestGetMetricForPath:
    """Tests for path-to-metric mapping."""

    def test_add_path(self) -> None:
        """Add path maps to add_calls metric."""
        assert _get_metric_for_path("/api/v2/memory/add") == "add_calls"

    def test_flush_path(self) -> None:
        """Flush path maps to flush_calls metric."""
        assert _get_metric_for_path("/api/v2/memory/flush") == "flush_calls"

    def test_search_path(self) -> None:
        """Search path maps to search_calls metric."""
        assert _get_metric_for_path("/api/v2/memory/search") == "search_calls"
        assert _get_metric_for_path("/api/v2/knowledge/search") == "search_calls"

    def test_get_path(self) -> None:
        """Get path maps to get_calls metric."""
        assert _get_metric_for_path("/api/v2/memory/get") == "get_calls"

    def test_unknown_path(self) -> None:
        """Unknown path returns None."""
        assert _get_metric_for_path("/api/v2/memory/delete") is None


class TestRequestLoggingMiddleware:
    """Tests for RequestLoggingMiddleware behavior."""

    def _make_middleware(
        self,
        *,
        retain_days: int = 14,
        exempt_paths: list[str] | None = None,
    ) -> RequestLoggingMiddleware:
        """Create middleware with test settings."""
        settings = RequestLogsSettings(retain_days=retain_days)
        return RequestLoggingMiddleware(
            MagicMock(),
            settings,
            exempt_paths=exempt_paths,
        )

    async def test_non_api_path_passes_through(self) -> None:
        """Non-API paths pass through without logging."""
        middleware = self._make_middleware()
        request = MagicMock()
        request.url.path = "/health"
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        call_next.assert_called_once()

    async def test_exempt_path_passes_through(self) -> None:
        """Exempt paths pass through without logging."""
        middleware = self._make_middleware(exempt_paths=["/health"])
        request = MagicMock()
        request.url.path = "/api/v2/health"
        request.app.state.control_store = None
        call_next = AsyncMock(return_value=Response())

        await middleware.dispatch(request, call_next)

        call_next.assert_called_once()

    async def test_api_path_logged(self) -> None:
        """API paths are logged to the control store."""
        middleware = self._make_middleware()
        store = AsyncMock()
        store.log_request = AsyncMock()
        store.increment_usage = AsyncMock()
        store.prune_request_logs = AsyncMock(return_value=0)

        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.method = "POST"
        request.state.request_id = "req_123"
        request.state.api_key_id = "key_456"
        request.headers = {}
        request.client = None
        request.app.state.control_store = store
        call_next = AsyncMock(return_value=Response(status_code=200))

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once()
