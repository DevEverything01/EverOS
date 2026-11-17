"""Unit tests for the request logging middleware."""

from __future__ import annotations

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.responses import Response

from everos.config import RequestLogsSettings
from everos.entrypoints.api.middleware.request_logging import (
    RequestLoggingMiddleware,
    RequestLogSink,
    RequestRecord,
    _extract_client_ip,
    _is_exempt,
    metric_for_path,
)


class TestExtractClientIp:
    """Tests for client IP extraction."""

    def test_x_forwarded_for_ignored_by_default(self) -> None:
        """A client-supplied X-Forwarded-For does not replace the peer address."""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        request.client.host = "10.0.0.1"

        ip = _extract_client_ip(request)

        assert ip == "10.0.0.1"

    def test_x_forwarded_for_left_most_hop_when_trusted(self) -> None:
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        request.client.host = "10.0.0.1"

        ip = _extract_client_ip(request, trust_forwarded_for=True)

        assert ip == "1.2.3.4"

    def test_x_forwarded_for_ipv6_when_trusted(self) -> None:
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "2001:db8::1"}
        request.client.host = "10.0.0.1"

        assert _extract_client_ip(request, trust_forwarded_for=True) == "2001:db8::1"

    def test_malformed_x_forwarded_for_falls_back_to_peer(self) -> None:
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "<script>, 5.6.7.8"}
        request.client.host = "10.0.0.1"

        ip = _extract_client_ip(request, trust_forwarded_for=True)

        assert ip == "10.0.0.1"

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
        assert metric_for_path("/api/v2/memory/add") == "add_calls"

    def test_flush_path(self) -> None:
        """Flush path maps to flush_calls metric."""
        assert metric_for_path("/api/v2/memory/flush") == "flush_calls"

    def test_search_path(self) -> None:
        """Search path maps to search_calls metric."""
        assert metric_for_path("/api/v2/memory/search") == "search_calls"
        assert metric_for_path("/api/v2/knowledge/search") == "search_calls"

    def test_get_path(self) -> None:
        """Get path maps to get_calls metric."""
        assert metric_for_path("/api/v2/memory/get") == "get_calls"

    def test_unknown_path(self) -> None:
        """Unknown path returns None."""
        assert metric_for_path("/api/v2/memory/delete") is None


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


def _record(store, request_id: str = "r", path: str = "/api/v2/memory/add"):
    return RequestRecord(
        store=store,
        request_id=request_id,
        method="POST",
        path=path,
        status=200,
        latency_ms=1,
        client=None,
        api_key_id=None,
        bytes_out=None,
    )


def _store() -> AsyncMock:
    store = AsyncMock()
    store.prune_request_logs = AsyncMock(return_value=0)
    return store


class TestRequestLogSink:
    """Bounded queue, drop counting and shutdown drain."""

    async def test_dispatch_does_not_write_on_request_path(self) -> None:
        """The record goes onto the sink's queue; only the worker writes it.

        Independent of when the event loop schedules the worker: every
        ``log_request`` call is checked to run in the worker task, never in
        the task that served the request.
        """
        sink = RequestLogSink(RequestLogsSettings())
        middleware = RequestLoggingMiddleware(
            MagicMock(), RequestLogsSettings(), sink=sink
        )
        writers: list[asyncio.Task | None] = []
        store = _store()
        store.log_request.side_effect = lambda **_: writers.append(
            asyncio.current_task()
        )
        submitted: list[RequestRecord] = []
        real_submit = sink.submit

        def spy_submit(record: RequestRecord) -> bool:
            accepted = real_submit(record)
            submitted.append(record)
            assert accepted
            return accepted

        sink.submit = spy_submit  # type: ignore[method-assign]
        request = MagicMock()
        request.url.path = "/api/v2/memory/add"
        request.method = "POST"
        request.state.request_id = "req_1"
        request.state.api_key_id = None
        request.headers = {}
        request.client = None
        request.app.state.control_store = store
        serving = asyncio.current_task()

        await middleware.dispatch(request, AsyncMock(return_value=Response()))

        assert [r.request_id for r in submitted] == ["req_1"]
        assert serving not in writers
        worker = sink._worker
        await sink.drain()
        store.log_request.assert_awaited_once()
        assert store.log_request.await_args.kwargs["request_id"] == "req_1"
        assert writers == [worker]

    async def test_full_queue_drops_and_counts(self) -> None:
        sink = RequestLogSink(RequestLogsSettings(queue_size=2))
        store = _store()

        accepted = [sink.submit(_record(store, f"r{i}")) for i in range(5)]

        assert accepted == [True, True, False, False, False]
        assert sink.dropped == 3
        await sink.drain()
        assert store.log_request.await_count == 2

    async def test_drain_writes_everything_queued(self) -> None:
        sink = RequestLogSink(RequestLogsSettings(queue_size=100))
        store = _store()
        for i in range(50):
            sink.submit(_record(store, f"r{i}"))

        await sink.drain()

        assert store.log_request.await_count == 50
        assert store.increment_usage.await_count == 50

    async def test_write_failure_does_not_stop_the_worker(self) -> None:
        sink = RequestLogSink(RequestLogsSettings())
        store = _store()
        store.log_request.side_effect = [RuntimeError("disk"), None]

        sink.submit(_record(store, "bad"))
        sink.submit(_record(store, "good"))
        await sink.drain()

        assert store.log_request.await_count == 2

    async def test_drain_without_traffic_is_a_noop(self) -> None:
        await RequestLogSink(RequestLogsSettings()).drain()

    def test_submit_without_event_loop_returns_false(self) -> None:
        """A sync caller (no running loop) gets False, not a RuntimeError."""
        sink = RequestLogSink(RequestLogsSettings())

        assert sink.submit(_record(_store(), "sync")) is False
        assert sink.dropped == 0

    async def test_drain_gives_up_on_a_stuck_write(self) -> None:
        sink = RequestLogSink(RequestLogsSettings(), drain_timeout_s=0.05)
        store = _store()
        never = asyncio.Event()

        async def stuck(**_: object) -> None:
            await never.wait()

        store.log_request.side_effect = stuck
        sink.submit(_record(store, "stuck"))
        sink.submit(_record(store, "behind"))

        await asyncio.wait_for(sink.drain(), timeout=2)

        assert store.log_request.await_count == 1
        assert sink._worker is None

    async def test_prune_runs_again_same_hour_next_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from everos.entrypoints.api.middleware import request_logging as module

        clock = [dt.datetime(2026, 11, 15, 9, 10, tzinfo=dt.UTC)]
        monkeypatch.setattr(module, "get_utc_now", lambda: clock[0])
        sink = RequestLogSink(RequestLogsSettings())
        store = _store()

        sink.submit(_record(store, "day1"))
        await asyncio.sleep(0)
        clock[0] = dt.datetime(2026, 11, 15, 9, 50, tzinfo=dt.UTC)
        sink.submit(_record(store, "day1-later"))
        await asyncio.sleep(0)
        clock[0] = dt.datetime(2026, 11, 16, 9, 5, tzinfo=dt.UTC)
        sink.submit(_record(store, "day2"))
        await sink.drain()

        assert store.prune_request_logs.await_count == 2


class TestForwardedForThroughApp:
    """End to end: the recorded client ignores a forged X-Forwarded-For."""

    async def test_forged_header_is_not_recorded(self, secured) -> None:
        headers = {"X-Api-Key": secured.read_key, "X-Forwarded-For": "6.6.6.6"}
        async with (
            secured.app.router.lifespan_context(secured.app),
            secured.client(headers=headers) as client,
        ):
            await client.get("/api/v2/memory/tags?memory_id=x_ep_20260101_1")

        items, _ = await secured.store.list_request_logs()
        assert [i.client for i in items] == ["127.0.0.1"]
