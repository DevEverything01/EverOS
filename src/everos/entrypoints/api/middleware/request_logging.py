"""Request logging middleware.

Records every ``/api/`` request (except exempt paths) to the control-plane
``request_logs`` table. Captures:
- Timestamp (UTC)
- Request ID (from :class:`RequestIdMiddleware`)
- Method and path (without query string)
- Response status code
- Latency in milliseconds
- Client IP
- API key ID (if authenticated)

The middleware also increments usage counters:
- ``add_calls``, ``flush_calls``, ``search_calls``, ``get_calls`` by route
- ``errors`` for any 5xx response

Prunes old logs based on ``[server.logs] retain_days`` on startup and hourly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from everos.config import RequestLogsSettings
from everos.core.observability.logging import get_logger
from everos.core.utils.datetime import get_utc_now

if TYPE_CHECKING:
    from everos.service.control import ControlStore

logger = get_logger(__name__)

DEFAULT_EXEMPT_PATHS = [
    "/health",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/console",
]

PATH_TO_METRIC = {
    "/memory/add": "add_calls",
    "/memory/flush": "flush_calls",
    "/memory/search": "search_calls",
    "/memory/get": "get_calls",
    "/knowledge/search": "search_calls",
}


def _is_exempt(path: str, exempt_paths: list[str]) -> bool:
    """Check if path matches any exempt prefix."""
    for exempt in exempt_paths:
        if path == exempt or path.startswith(exempt + "/"):
            return True
    return False


def _extract_client_ip(request: Request) -> str | None:
    """Extract client IP from request, handling proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _get_metric_for_path(path: str) -> str | None:
    """Map a request path to its usage metric."""
    for pattern, metric in PATH_TO_METRIC.items():
        if pattern in path:
            return metric
    return None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs requests to the control-plane database.

    Captures request metadata and response status for audit and analytics.
    Also increments usage counters for metering.

    Args:
        app: The ASGI application to wrap.
        settings: Request log retention configuration.
        exempt_paths: Paths to skip logging (health checks, etc.).
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[Response]],
        settings: RequestLogsSettings,
        exempt_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._exempt_paths = exempt_paths or DEFAULT_EXEMPT_PATHS
        self._last_prune_hour = -1
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _get_store(self, request: Request) -> ControlStore | None:
        """Get the control store from app state, or None if not available."""
        return getattr(request.app.state, "control_store", None)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        if not path.startswith("/api/"):
            return await call_next(request)

        if _is_exempt(path, self._exempt_paths):
            return await call_next(request)

        store = self._get_store(request)

        start = get_utc_now()
        response = await call_next(request)
        end = get_utc_now()

        latency_ms = int((end - start).total_seconds() * 1000)
        request_id = getattr(request.state, "request_id", "unknown")
        api_key_id = getattr(request.state, "api_key_id", None)
        client_ip = _extract_client_ip(request)
        content_length = response.headers.get("content-length")
        bytes_out = int(content_length) if content_length else None

        path_no_query = path.split("?")[0]

        if store is not None:
            task = asyncio.create_task(
                self._log_and_meter(
                    store=store,
                    request_id=request_id,
                    method=request.method,
                    path=path_no_query,
                    status=response.status_code,
                    latency_ms=latency_ms,
                    client=client_ip,
                    api_key_id=api_key_id,
                    bytes_out=bytes_out,
                )
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        return response

    async def _log_and_meter(
        self,
        *,
        store: ControlStore,
        request_id: str,
        method: str,
        path: str,
        status: int,
        latency_ms: int,
        client: str | None,
        api_key_id: str | None,
        bytes_out: int | None,
    ) -> None:
        """Log the request and increment usage counters."""
        try:
            await store.log_request(
                request_id=request_id,
                method=method,
                path=path,
                status=status,
                latency_ms=latency_ms,
                client=client,
                api_key_id=api_key_id,
                bytes_out=bytes_out,
            )

            metric = _get_metric_for_path(path)
            if metric:
                await store.increment_usage(metric)

            if status >= 500:
                await store.increment_usage("errors")

            await self._maybe_prune(store)

        except Exception:
            logger.warning("request_log_failed", request_id=request_id, exc_info=True)

    async def _maybe_prune(self, store: ControlStore) -> None:
        """Prune old logs if an hour has passed since last prune."""
        current_hour = get_utc_now().hour
        if current_hour != self._last_prune_hour:
            self._last_prune_hour = current_hour
            try:
                deleted = await store.prune_request_logs(self._settings.retain_days)
                if deleted > 0:
                    logger.info(
                        "request_logs_pruned",
                        deleted=deleted,
                        retain_days=self._settings.retain_days,
                    )
            except Exception:
                logger.warning("request_logs_prune_failed", exc_info=True)
