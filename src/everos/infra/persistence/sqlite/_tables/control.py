"""Control-plane SQLModel tables.

Tables for the control plane living in ``control.db``:

- ``api_keys``: API key registry (hash stored, secret never persisted)
- ``request_logs``: per-request audit log
- ``usage_daily``: daily usage counters by metric
- ``feedback``: user feedback on memories
- ``tags``: user-assigned tags on memories
- ``schema_version``: migration version tracking (one row)

All timestamps are UTC. The ``api_keys`` table stores a sha256 hex hash of the
full secret; only the 8-character prefix is retrievable for display.

Uses :class:`UtcDateTimeColumn` from the base module for all datetime fields,
enforcing the storage-UTC discipline uniformly across write/read paths.
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel

from everos.core.utils.datetime import UtcDatetime, get_utc_now
from everos.infra.persistence.sqlite._base.base import UtcDateTimeColumn


class SchemaVersion(SQLModel, table=True):
    """Single-row table tracking the control.db schema version."""

    __tablename__ = "schema_version"

    id: int = Field(default=1, primary_key=True)
    version: int = Field(default=1)
    migrated_at: UtcDatetime = Field(
        default_factory=get_utc_now, sa_type=UtcDateTimeColumn
    )


class ApiKey(SQLModel, table=True):
    """API key registry. The secret is never stored; only its sha256 hash."""

    __tablename__ = "api_keys"

    key_id: str = Field(primary_key=True)
    name: str
    prefix: str = Field(index=True)
    hash: str = Field(index=True)
    scopes: str = Field(default="[]")
    created_at: UtcDatetime = Field(
        default_factory=get_utc_now, sa_type=UtcDateTimeColumn
    )
    updated_at: UtcDatetime = Field(
        default_factory=get_utc_now,
        sa_type=UtcDateTimeColumn,
        sa_column_kwargs={"onupdate": get_utc_now},
    )
    last_used_at: UtcDatetime | None = Field(default=None, sa_type=UtcDateTimeColumn)
    revoked_at: UtcDatetime | None = Field(
        default=None, index=True, sa_type=UtcDateTimeColumn
    )


class RequestLog(SQLModel, table=True):
    """Per-request audit log entry."""

    __tablename__ = "request_logs"

    id: int | None = Field(default=None, primary_key=True)
    ts: UtcDatetime = Field(
        default_factory=get_utc_now, index=True, sa_type=UtcDateTimeColumn
    )
    request_id: str = Field(index=True)
    method: str
    path: str
    status: int = Field(index=True)
    latency_ms: int
    client: str | None = Field(default=None)
    api_key_id: str | None = Field(default=None, index=True)
    bytes_out: int | None = Field(default=None)


class UsageDaily(SQLModel, table=True):
    """Daily usage counter by metric. Composite PK (day, metric)."""

    __tablename__ = "usage_daily"

    day: str = Field(primary_key=True)
    metric: str = Field(primary_key=True)
    value: int = Field(default=0)


class Feedback(SQLModel, table=True):
    """User feedback on a memory (thumbs up/down + optional comment)."""

    __tablename__ = "feedback"

    id: int | None = Field(default=None, primary_key=True)
    memory_id: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    rating: int
    comment: str | None = Field(default=None)
    created_at: UtcDatetime = Field(
        default_factory=get_utc_now, sa_type=UtcDateTimeColumn
    )


class Tag(SQLModel, table=True):
    """User-assigned tag on a memory. Composite PK (memory_id, tag)."""

    __tablename__ = "tags"

    memory_id: str = Field(primary_key=True)
    tag: str = Field(primary_key=True)
    user_id: str | None = Field(default=None)
    created_at: UtcDatetime = Field(
        default_factory=get_utc_now, sa_type=UtcDateTimeColumn
    )
