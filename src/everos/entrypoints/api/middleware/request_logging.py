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

Records are not written on the request path: the middleware puts them on a
bounded :class:`RequestLogSink` queue (``[request_logs] queue_size``) drained
by one worker task. A full queue drops the record and counts it (logged as
``request_log_dropped``); the app's shutdown drains what is queued, waiting
at most :data:`DRAIN_TIMEOUT_S`.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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

DRAIN_TIMEOUT_S = 5.0
"""Longest a shutdown waits for queued request logs to be written."""

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


def _valid_ip(value: str) -> str | None:
    """``value`` if it parses as an IPv4/IPv6 address, else None."""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _extract_client_ip(
    request: Request, *, trust_forwarded_for: bool = False
) -> str | None:
    """The client address to record for ``request``.

    The socket peer by default. ``X-Forwarded-For`` is client-controlled, so
    it is used only with ``trust_forwarded_for`` (behind a proxy that sets
    it), and then only its left-most hop, and only if it is a valid IP.
    """
    if trust_forwarded_for:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = _valid_ip(forwarded.split(",")[0].strip())
            if ip is not None:
                return ip
    if request.client:
        return request.client.host
    return None


def metric_for_path(path: str) -> str | None:
    """Map a request path to its usage metric."""
    for pattern, metric in PATH_TO_METRIC.items():
        if pattern in path:
            return metric
    return None


@dataclass(frozen=True)
class RequestRecord:
    """One request to log, captured after the response was produced."""

    store: ControlStore
    request_id: str
    method: str
    path: str
    status: int
    latency_ms: int
    client: str | None
    api_key_id: str | None
    bytes_out: int | None


class RequestLogSink:
    """Bounded queue + single worker that writes request logs and usage.

    Args:
        settings: ``[request_logs]`` (queue size, retention).
        drain_timeout_s: Longest :meth:`drain` waits for the queue to empty.
    """

    def __init__(
        self,
        settings: RequestLogsSettings,
        *,
        drain_timeout_s: float = DRAIN_TIMEOUT_S,
    ) -> None:
        self._settings = settings
        self._drain_timeout_s = drain_timeout_s
        self._queue: asyncio.Queue[RequestRecord] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_prune: tuple[dt.date, int] | None = None
        self.dropped = 0
        """Records discarded because the queue was full (process lifetime)."""

    def _ensure_worker(self) -> asyncio.Queue[RequestRecord]:
        loop = asyncio.get_running_loop()
        if self._queue is None or self._loop is not loop:
            self._loop = loop
            self._queue = asyncio.Queue(maxsize=self._settings.queue_size)
            self._worker = loop.create_task(self._run(self._queue))
        return self._queue

    def submit(self, record: RequestRecord) -> bool:
        """Queue ``record``; False when it cannot be queued.

        False (and counted) when the queue is full; False (and logged) when
        called outside a running event loop, where no worker can run.
        """
        try:
            queue = self._ensure_worker()
        except RuntimeError:
            logger.warning("request_log_no_event_loop", request_id=record.request_id)
            return False
        try:
            queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.warning(
                "request_log_dropped",
                request_id=record.request_id,
                dropped_total=self.dropped,
                queue_size=self._settings.queue_size,
            )
            return False
        return True

    async def drain(self) -> None:
        """Write what is queued, then stop the worker (app shutdown).

        Waits at most ``drain_timeout_s`` seconds: a stuck SQLite write must not hang
        the shutdown. Whatever is still queued then is abandoned and logged
        as ``request_logs_drain_timeout``.
        """
        queue, worker = self._queue, self._worker
        if queue is None or worker is None:
            return
        if self._loop is not asyncio.get_running_loop():
            return
        try:
            async with asyncio.timeout(self._drain_timeout_s):
                await queue.join()
        except TimeoutError:
            logger.warning(
                "request_logs_drain_timeout",
                timeout_s=self._drain_timeout_s,
                abandoned=queue.qsize(),
            )
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        self._queue = self._worker = self._loop = None
        if self.dropped:
            logger.warning("request_log_dropped_summary", dropped_total=self.dropped)

    async def _run(self, queue: asyncio.Queue[RequestRecord]) -> None:
        while True:
            record = await queue.get()
            try:
                await self._write(record)
            finally:
                queue.task_done()

    async def _write(self, record: RequestRecord) -> None:
        """Log the request and increment usage counters."""
        store = record.store
        try:
            await store.log_request(
                request_id=record.request_id,
                method=record.method,
                path=record.path,
                status=record.status,
                latency_ms=record.latency_ms,
                client=record.client,
                api_key_id=record.api_key_id,
                bytes_out=record.bytes_out,
            )
            metric = metric_for_path(record.path)
            if metric:
                await store.increment_usage(metric)
            if record.status >= 500:
                await store.increment_usage("errors")
            await self._maybe_prune(store)
        except Exception:
            logger.warning(
                "request_log_failed", request_id=record.request_id, exc_info=True
            )

    async def _maybe_prune(self, store: ControlStore) -> None:
        """Prune old logs once per UTC (date, hour).

        Keyed by the date too: comparing the hour alone skipped every prune
        when the only requests of two days fell in the same hour.
        """
        now = get_utc_now()
        current = (now.date(), now.hour)
        if current != self._last_prune:
            self._last_prune = current
            try:
                await store.prune_request_logs(self._settings.retain_days)
            except Exception:
                logger.warning("request_logs_prune_failed", exc_info=True)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs requests to the control-plane database.

    Captures request metadata and response status for audit and analytics,
    and hands the record to a :class:`RequestLogSink` (never writes on the
    request path).

    Args:
        app: The ASGI application to wrap.
        settings: Request log configuration.
        exempt_paths: Paths to skip logging (health checks, etc.).
        sink: The queue the records go to; ``create_app`` shares one with the
            lifespan so shutdown can drain it. Built from ``settings`` if None.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[Response]],
        settings: RequestLogsSettings,
        exempt_paths: list[str] | None = None,
        sink: RequestLogSink | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._exempt_paths = exempt_paths or DEFAULT_EXEMPT_PATHS
        self.sink = sink or RequestLogSink(settings)

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

        if store is not None:
            content_length = response.headers.get("content-length")
            self.sink.submit(
                RequestRecord(
                    store=store,
                    request_id=getattr(request.state, "request_id", "unknown"),
                    method=request.method,
                    path=path.split("?")[0],
                    status=response.status_code,
                    latency_ms=int((end - start).total_seconds() * 1000),
                    client=_extract_client_ip(
                        request,
                        trust_forwarded_for=self._settings.trust_forwarded_for,
                    ),
                    api_key_id=getattr(request.state, "api_key_id", None),
                    bytes_out=int(content_length) if content_length else None,
                )
            )

        return response
