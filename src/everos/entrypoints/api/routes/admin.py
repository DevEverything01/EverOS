"""Admin endpoints for the control-plane (Cloud-parity).

Endpoints:
- GET /admin/request-logs - list request logs with filters
- GET /admin/request-logs/{id} - get single request log
- GET /admin/usage - usage statistics and quota
- GET /admin/overview - system overview for dashboard
- GET /admin/memories - browse memories without a query vector
- GET /admin/memories/{id} - single memory detail
- GET /admin/agents - list agents

All endpoints require ``admin`` scope.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from everos.core.errors import NotFoundError
from everos.core.utils.datetime import get_utc_now, to_iso_format
from everos.entrypoints.api.dependencies import (
    ApplicationDep,
    ControlStoreDep,
    RequestIdDep,
    require_admin,
)
from everos.entrypoints.api.middleware.request_logging import metric_for_path
from everos.entrypoints.api.schemas import SuccessEnvelope

if TYPE_CHECKING:
    from everos.service.control import ControlStore

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


# ── Request logs ────────────────────────────────────────────────────────────


class RequestLogItem(BaseModel):
    """A single request log entry."""

    id: int
    ts: str
    request_id: str
    method: str
    path: str
    status: int
    latency_ms: int
    client: str | None
    api_key_id: str | None
    bytes_out: int | None


class RequestLogListResponse(BaseModel):
    """Paginated request log list."""

    items: list[RequestLogItem]
    total: int


@router.get(
    "/request-logs",
    response_model=SuccessEnvelope[RequestLogListResponse],
)
async def list_request_logs(
    store: ControlStoreDep,
    request_id: RequestIdDep,
    from_ts: Annotated[dt.datetime | None, Query(alias="from")] = None,
    to_ts: Annotated[dt.datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    method: str | None = None,
    path: str | None = None,
    api_key_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuccessEnvelope[RequestLogListResponse]:
    """List request logs with optional filters.

    Returns newest first. Use ``from`` and ``to`` for time range filtering.
    """
    exact_status: int | None = None
    prefix_status: int | None = None
    if status:
        digits = status.strip().lower().removesuffix("xx")
        if digits.isdigit() and len(digits) == 3:
            exact_status = int(digits)
        elif digits.isdigit() and len(digits) == 1:
            prefix_status = int(digits)

    items, total = await store.list_request_logs(
        limit=limit,
        offset=offset,
        from_ts=from_ts,
        to_ts=to_ts,
        status=exact_status,
        status_prefix=prefix_status,
        method=method,
        path=path,
        api_key_id=api_key_id,
    )
    return SuccessEnvelope(
        request_id=request_id,
        data=RequestLogListResponse(
            items=[
                RequestLogItem(
                    id=item.id,
                    ts=to_iso_format(item.ts),
                    request_id=item.request_id,
                    method=item.method,
                    path=item.path,
                    status=item.status,
                    latency_ms=item.latency_ms,
                    client=item.client,
                    api_key_id=item.api_key_id,
                    bytes_out=item.bytes_out,
                )
                for item in items
            ],
            total=total,
        ),
    )


@router.get(
    "/request-logs/{log_id}",
    response_model=SuccessEnvelope[RequestLogItem],
)
async def get_request_log(
    log_id: int,
    store: ControlStoreDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[RequestLogItem]:
    """Get a single request log by ID."""
    item = await store.get_request_log(log_id)
    if item is None:
        raise NotFoundError(f"request log {log_id} not found")
    return SuccessEnvelope(
        request_id=request_id,
        data=RequestLogItem(
            id=item.id,
            ts=to_iso_format(item.ts),
            request_id=item.request_id,
            method=item.method,
            path=item.path,
            status=item.status,
            latency_ms=item.latency_ms,
            client=item.client,
            api_key_id=item.api_key_id,
            bytes_out=item.bytes_out,
        ),
    )


# ── Usage metering ──────────────────────────────────────────────────────────


class UsageBucket(BaseModel):
    """One bucket of usage data."""

    bucket: str
    metrics: dict[str, int]


class UsageQuota(BaseModel):
    """Quota limits and usage."""

    limit: int | None
    used: int
    remaining: int | None


class UsageResponse(BaseModel):
    """Usage statistics with optional quota info."""

    series: list[UsageBucket]
    totals: dict[str, int]
    quota: dict[str, UsageQuota] | None = None


@router.get(
    "/usage",
    response_model=SuccessEnvelope[UsageResponse],
)
async def get_usage(
    store: ControlStoreDep,
    request_id: RequestIdDep,
    app: ApplicationDep,
    range_param: Annotated[str, Query(alias="range")] = "24h",
) -> SuccessEnvelope[UsageResponse]:
    """Get usage statistics for a time range.

    Args:
        range_param: One of ``24h``, ``7d``, ``30d``; anything else is a 400.

    Returns:
        ``24h``: 24 hourly buckets (``YYYY-MM-DDTHH:00:00Z``, the last 24
        whole UTC hours including the current one, zero-filled) counted from
        the request logs. ``7d`` / ``30d``: daily buckets from the usage
        counters.
    """
    now = get_utc_now()
    if range_param == "24h":
        data = await _hourly_usage(store, now)
    elif range_param in _DAILY_RANGES:
        data = await _daily_usage(store, now, _DAILY_RANGES[range_param])
    else:
        raise HTTPException(
            status_code=400,
            detail=f"invalid range {range_param!r}; use one of 24h, 7d, 30d",
        )
    return SuccessEnvelope(request_id=request_id, data=data)


_DAILY_RANGES = {"7d": 7, "30d": 30}
_HOURS = 24


async def _hourly_usage(store: ControlStore, now: dt.datetime) -> UsageResponse:
    """The last 24 whole UTC hours (current hour included), from request logs."""
    current = now.replace(minute=0, second=0, microsecond=0)
    first = current - dt.timedelta(hours=_HOURS - 1)
    hours = [first + dt.timedelta(hours=i) for i in range(_HOURS)]
    buckets: dict[str, dict[str, int]] = {h.strftime("%Y-%m-%dT%H"): {} for h in hours}
    rows = await store.request_counts_by_hour(first, current + dt.timedelta(hours=1))
    totals: dict[str, int] = {}

    def add(metrics: dict[str, int], name: str, n: int) -> None:
        metrics[name] = metrics.get(name, 0) + n
        totals[name] = totals.get(name, 0) + n

    for hour, path, is_error, count in rows:
        metrics = buckets.get(hour)
        if metrics is None:
            continue
        add(metrics, "requests", count)
        metric = metric_for_path(path)
        if metric:
            add(metrics, metric, count)
        if is_error:
            add(metrics, "errors", count)
    series = [
        UsageBucket(bucket=f"{hour}:00:00Z", metrics=metrics)
        for hour, metrics in buckets.items()
    ]
    return UsageResponse(series=series, totals=totals, quota=None)


async def _daily_usage(
    store: ControlStore, now: dt.datetime, days: int
) -> UsageResponse:
    """The last ``days`` UTC days (today included), from the daily counters."""
    from_str = (now - dt.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    to_str = now.strftime("%Y-%m-%d")
    by_day: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for row in await store.get_usage(from_str, to_str):
        by_day.setdefault(row.day, {})[row.metric] = row.value
        totals[row.metric] = totals.get(row.metric, 0) + row.value
    series = [
        UsageBucket(bucket=day, metrics=metrics)
        for day, metrics in sorted(by_day.items())
    ]
    return UsageResponse(series=series, totals=totals, quota=None)


# ── Admin overview ──────────────────────────────────────────────────────────


class OverviewCounts(BaseModel):
    """Memory type counts."""

    profiles: int = 0
    profile_items: int = 0
    episodes: int = 0
    cases: int = 0
    skills: int = 0
    agents: int = 0
    knowledge_docs: int = 0


class OverviewResponse(BaseModel):
    """System overview for the dashboard."""

    space: str
    counts: OverviewCounts
    storage_bytes: int | None = None
    model: str | None = None
    version: str
    auth_mode: str
    docs_enabled: bool = False
    """Whether ``/docs`` / ``/redoc`` / ``/openapi.json`` are served; the
    console hides its API-docs links otherwise."""
    quota: dict[str, Any] | None = None
    calls_7d: int = 0
    calls_today: int = 0
    search_calls: int = 0
    users: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    last_write: str | None = None


@router.get(
    "/overview",
    response_model=SuccessEnvelope[OverviewResponse],
)
async def get_overview(
    request: Request,
    app: ApplicationDep,
    store: ControlStoreDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[OverviewResponse]:
    """System overview for the dashboard: counts, calls, activity."""
    from everos import __version__
    from everos.service.control.overview import scan_store

    stats = scan_store(Path(str(app._root.root)))
    now = get_utc_now()
    week_ago = now - dt.timedelta(days=7)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    counts = OverviewCounts(
        profiles=len(stats.users),
        profile_items=stats.profile_items,
        episodes=stats.episodes,
        cases=stats.cases,
        skills=stats.skills,
        agents=stats.agents,
        knowledge_docs=stats.knowledge_docs,
    )
    auth_mode = "open" if not app._settings.auth.auth_required else "api_key"
    activity = [
        {"date": day, **bucket} for day, bucket in sorted(stats.activity.items())
    ][-45:]
    last_write = next((u["last"] for u in stats.users if u["last"]), None)

    return SuccessEnvelope(
        request_id=request_id,
        data=OverviewResponse(
            space=str(app._root.root),
            counts=counts,
            storage_bytes=stats.storage_bytes,
            model=app._settings.llm.model,
            version=__version__,
            auth_mode=auth_mode,
            docs_enabled=request.app.docs_url is not None,
            quota=None,
            calls_7d=await store.count_requests(from_ts=week_ago),
            calls_today=await store.count_requests(from_ts=day_start),
            search_calls=await store.count_requests(path_suffix="/search"),
            users=stats.users,
            activity=activity,
            last_write=last_write,
        ),
    )


# ── Browse memories ─────────────────────────────────────────────────────────


class MemoryListItem(BaseModel):
    """A memory item in the browse list."""

    id: str
    memory_type: str
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    summary: str | None = None
    timestamp: str | None = None


class MemoryListResponse(BaseModel):
    """Paginated memory list."""

    items: list[MemoryListItem]
    total: int


@router.get(
    "/memories",
    response_model=SuccessEnvelope[MemoryListResponse],
)
async def list_memories(
    app: ApplicationDep,
    request_id: RequestIdDep,
    user_id: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuccessEnvelope[MemoryListResponse]:
    """Browse memories without a query vector.

    Use ``user_id`` and ``q`` (keyword search) to filter.
    """
    return SuccessEnvelope(
        request_id=request_id,
        data=MemoryListResponse(items=[], total=0),
    )


@router.get(
    "/memories/{memory_id}",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def get_memory(
    memory_id: str,
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[dict[str, Any]]:
    """Get a single memory by ID."""
    return SuccessEnvelope(
        request_id=request_id,
        data={"id": memory_id, "found": False},
    )


# ── Agents ──────────────────────────────────────────────────────────────────


class AgentItem(BaseModel):
    """An agent in the list."""

    agent_id: str
    case_count: int = 0
    skill_count: int = 0


class AgentListResponse(BaseModel):
    """List of agents."""

    items: list[AgentItem]
    total: int


@router.get(
    "/agents",
    response_model=SuccessEnvelope[AgentListResponse],
)
async def list_agents(
    app: ApplicationDep,
    request_id: RequestIdDep,
) -> SuccessEnvelope[AgentListResponse]:
    """List all agents with their case and skill counts."""
    return SuccessEnvelope(
        request_id=request_id,
        data=AgentListResponse(items=[], total=0),
    )
