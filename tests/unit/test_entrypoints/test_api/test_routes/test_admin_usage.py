"""``GET /admin/usage``: real hourly buckets for ``24h``; bad ranges are 400.

Before: ``24h`` queried whole UTC days, so just after midnight it counted all
of yesterday (up to ~48h), and any unknown ``range`` silently became ``24h``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from everos.entrypoints.api.app import create_app
from everos.infra.persistence.sqlite import ControlStore
from everos.infra.persistence.sqlite._tables.control import RequestLog
from tests.fakes import make_application

NOW = dt.datetime(2026, 11, 16, 0, 30, tzinfo=dt.UTC)


async def _log(store: ControlStore, ts: dt.datetime, path: str, status: int = 200):
    async with store.session_factory() as session:
        session.add(
            RequestLog(
                ts=ts,
                request_id=f"r{ts.isoformat()}{path}",
                method="POST",
                path=path,
                status=status,
                latency_ms=1,
            )
        )
        await session.commit()


@pytest.fixture
def app(control_store_db: ControlStore, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("everos.entrypoints.api.routes.admin.get_utc_now", lambda: NOW)
    return create_app(
        application=make_application(root=tmp_path, control=control_store_db)
    )


async def _usage(app, range_param: str):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/v2/admin/usage?range={range_param}")


async def test_24h_is_24_hourly_buckets(app, control_store_db) -> None:
    response = await _usage(app, "24h")

    assert response.status_code == 200
    series = response.json()["data"]["series"]
    assert len(series) == 24
    assert series[0]["bucket"] == "2026-11-15T01:00:00Z"
    assert series[-1]["bucket"] == "2026-11-16T00:00:00Z"
    assert all(b["metrics"] == {} for b in series)


async def test_24h_excludes_older_hours_of_yesterday(app, control_store_db) -> None:
    await _log(control_store_db, NOW - dt.timedelta(minutes=20), "/api/v2/memory/add")
    await _log(
        control_store_db, NOW - dt.timedelta(minutes=50), "/api/v2/memory/search"
    )
    await _log(
        control_store_db,
        dt.datetime(2026, 11, 15, 1, 5, tzinfo=dt.UTC),
        "/api/v2/memory/add",
        status=503,
    )
    # Same UTC day as the window start, but before it: must not count.
    await _log(
        control_store_db,
        dt.datetime(2026, 11, 15, 0, 50, tzinfo=dt.UTC),
        "/api/v2/memory/add",
    )

    response = await _usage(app, "24h")

    data = response.json()["data"]
    by_bucket = {b["bucket"]: b["metrics"] for b in data["series"]}
    assert by_bucket["2026-11-16T00:00:00Z"] == {"requests": 1, "add_calls": 1}
    assert by_bucket["2026-11-15T23:00:00Z"] == {"requests": 1, "search_calls": 1}
    assert by_bucket["2026-11-15T01:00:00Z"] == {
        "requests": 1,
        "add_calls": 1,
        "errors": 1,
    }
    assert data["totals"] == {
        "requests": 3,
        "add_calls": 2,
        "search_calls": 1,
        "errors": 1,
    }


@pytest.mark.parametrize("bad", ["1h", "48h", "bogus", "7D", ""])
async def test_invalid_range_is_400(app, bad: str) -> None:
    response = await _usage(app, bad)

    assert response.status_code == 400


async def test_7d_uses_daily_counters(app, control_store_db) -> None:
    from sqlalchemy import text

    async with control_store_db.session_factory() as session:
        for day, value in [("2026-11-16", 2), ("2026-11-10", 5), ("2026-11-09", 7)]:
            await session.execute(
                text(
                    "INSERT INTO usage_daily (day, metric, value) "
                    "VALUES (:d, 'add_calls', :v)"
                ),
                {"d": day, "v": value},
            )
        await session.commit()

    response = await _usage(app, "7d")

    assert response.status_code == 200
    data = response.json()["data"]
    assert [b["bucket"] for b in data["series"]] == ["2026-11-10", "2026-11-16"]
    assert data["totals"] == {"add_calls": 7}
