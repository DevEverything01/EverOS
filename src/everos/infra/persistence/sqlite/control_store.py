"""Control-plane storage: database manager and CRUD operations.

:class:`ControlStore` manages the ``control.db`` SQLite database, providing
async operations for API keys, request logs, usage metrics, feedback and tags.
It owns its own SQLAlchemy engine and session factory, independent from the
memory-domain ``system.db``.

``control.db`` holds the console session signing secret and API key hashes,
so it and its WAL / shared-memory files are kept owner-only (0600).

Schema creation is idempotent: calling :meth:`create_schema` multiple times
creates missing tables and columns without losing data. A ``schema_version``
row tracks the current version for forward-only migrations.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
from base64 import urlsafe_b64encode
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import Connection, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

from everos.config import SqliteSettings
from everos.core.observability.logging import get_logger
from everos.core.utils.datetime import get_utc_now

from ._base.engine import create_system_engine
from ._base.session import create_session_factory
from ._tables.control import (
    ApiKey,
    ControlSetting,
    Feedback,
    RequestLog,
    Tag,
    UsageDaily,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

CURRENT_SCHEMA_VERSION = 1
KEY_PREFIX_LEN = 8
KEY_SECRET_BYTES = 32


@dataclass(frozen=True)
class CreatedKey:
    """Result of creating an API key; ``secret`` is only available once."""

    key_id: str
    name: str
    prefix: str
    secret: str
    scopes: list[str]
    created_at: dt.datetime


@dataclass(frozen=True)
class KeyInfo:
    """API key info without the secret (for listing)."""

    key_id: str
    name: str
    prefix: str
    scopes: list[str]
    created_at: dt.datetime
    updated_at: dt.datetime
    last_used_at: dt.datetime | None
    revoked_at: dt.datetime | None


def _generate_key() -> tuple[str, str, str]:
    """Generate an API key: (prefix, secret, hash).

    Format: ``eos_<8-char prefix>_<32-byte urlsafe base64>``
    """
    raw = secrets.token_bytes(KEY_SECRET_BYTES)
    suffix = urlsafe_b64encode(raw).decode().rstrip("=")
    prefix = suffix[:KEY_PREFIX_LEN]
    secret = f"eos_{prefix}_{suffix}"
    key_hash = hashlib.sha256(secret.encode()).hexdigest()
    return prefix, secret, key_hash


_SQLITE_SIDECARS = ("", "-wal", "-shm", "-journal")


def _restrict_to_owner(db_path: Path) -> None:
    """Create ``db_path`` 0600 if missing; chmod it and its sidecars to 0600.

    SQLite creates ``-wal`` / ``-shm`` with the database file's mode, so a
    database that is 0600 before the first connection keeps every sidecar
    owner-only; the chmod after the connection covers pre-existing files.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(db_path, os.O_RDWR | os.O_CREAT, 0o600))
    for suffix in _SQLITE_SIDECARS:
        path = db_path.with_name(db_path.name + suffix)
        try:
            os.chmod(path, 0o600)
        except FileNotFoundError:
            continue


def _hash_key(secret: str) -> str:
    """Hash an API key secret with sha256."""
    return hashlib.sha256(secret.encode()).hexdigest()


class ControlStore:
    """Manager for the control-plane ``control.db`` database.

    Args:
        db_path: Path to the ``control.db`` file.
        settings: SQLite tunables (same as ``system.db``).
    """

    def __init__(self, db_path: Path, settings: SqliteSettings) -> None:
        self._db_path = db_path
        self._engine = create_system_engine(db_path, settings)
        self._session_factory = create_session_factory(self._engine)

    @property
    def engine(self) -> AsyncEngine:
        """The async engine; connects lazily on the first statement."""
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Opens sessions bound to :attr:`engine`."""
        return self._session_factory

    async def create_schema(self) -> int:
        """Create tables and run migrations idempotently.

        Also restricts ``control.db`` and its ``-wal`` / ``-shm`` siblings to
        the owner (0600) on every open, which fixes databases created by an
        earlier version under a permissive umask.

        Returns:
            The number of tables created or verified.
        """
        await anyio.to_thread.run_sync(_restrict_to_owner, self._db_path)
        async with self._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            await conn.run_sync(self._ensure_schema_version)
        await anyio.to_thread.run_sync(_restrict_to_owner, self._db_path)
        logger.info("control_schema_ready", version=CURRENT_SCHEMA_VERSION)
        return len(SQLModel.metadata.tables)

    def _ensure_schema_version(self, conn: Connection) -> None:
        """Insert or update the schema_version row."""
        result = conn.execute(
            text("SELECT version FROM schema_version WHERE id = 1")
        ).fetchone()
        if result is None:
            conn.execute(
                text(
                    "INSERT INTO schema_version (id, version, migrated_at) "
                    "VALUES (1, :version, :ts)"
                ),
                {"version": CURRENT_SCHEMA_VERSION, "ts": get_utc_now()},
            )
        elif result[0] < CURRENT_SCHEMA_VERSION:
            conn.execute(
                text(
                    "UPDATE schema_version SET version = :version, "
                    "migrated_at = :ts WHERE id = 1"
                ),
                {"version": CURRENT_SCHEMA_VERSION, "ts": get_utc_now()},
            )

    async def dispose(self) -> None:
        """Close all pooled connections. Idempotent."""
        await self._engine.dispose()
        logger.info("control_engine_disposed", db_path=str(self._db_path))

    # ── API Keys ────────────────────────────────────────────────────────────

    # ── Control settings ────────────────────────────────────────────────────

    async def get_setting(self, key: str) -> str | None:
        """Return the persisted value for ``key``, or None."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(ControlSetting.value).where(ControlSetting.key == key)
            )
            return result.scalar_one_or_none()

    async def get_or_create_setting(self, key: str, value: str) -> str:
        """Return the persisted value for ``key``, storing ``value`` if absent.

        ``INSERT OR IGNORE`` then ``SELECT``: whichever writer commits first
        wins, and every caller (any process) reads that same value back.
        """
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT OR IGNORE INTO control_settings (key, value, created_at) "
                    "VALUES (:key, :value, :created_at)"
                ),
                {"key": key, "value": value, "created_at": get_utc_now()},
            )
            await session.commit()
            result = await session.execute(
                select(ControlSetting.value).where(ControlSetting.key == key)
            )
            return result.scalar_one()

    # ── API Keys ────────────────────────────────────────────────────────────

    async def create_key(
        self,
        name: str,
        scopes: list[str] | None = None,
    ) -> CreatedKey:
        """Create a new API key.

        Args:
            name: Human-readable name for the key.
            scopes: List of scopes (default: all standard scopes).

        Returns:
            The created key including the secret (only returned once).
        """
        row, created = self._new_key_row(name, scopes)
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

        logger.info("api_key_created", key_id=created.key_id, name=name)
        return created

    async def create_key_once(
        self,
        claim: str,
        name: str,
        scopes: list[str] | None = None,
    ) -> CreatedKey | None:
        """Create a key only if ``claim`` was never taken; else return None.

        The claim row (in ``control_settings``) and the key are written in one
        transaction, so of any number of concurrent callers -- in this process
        or another -- exactly one creates the key.
        """
        row, created = self._new_key_row(name, scopes)
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "INSERT OR IGNORE INTO control_settings (key, value, created_at) "
                    "VALUES (:key, :value, :created_at)"
                ),
                {
                    "key": claim,
                    "value": created.key_id,
                    "created_at": get_utc_now(),
                },
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            session.add(row)
            await session.commit()

        logger.info("api_key_created", key_id=created.key_id, name=name, claim=claim)
        return created

    async def abandon_key_once(self, claim: str, key_id: str) -> None:
        """Undo :meth:`create_key_once`: revoke ``key_id`` and release ``claim``.

        One transaction; the claim row is only deleted while it still names
        ``key_id``, so a later winner's claim is never released by mistake.
        """
        now = get_utc_now()
        async with self._session_factory() as session:
            await session.execute(
                update(ApiKey)
                .where(ApiKey.key_id == key_id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=now, updated_at=now)
            )
            await session.execute(
                delete(ControlSetting).where(
                    ControlSetting.key == claim, ControlSetting.value == key_id
                )
            )
            await session.commit()
        logger.warning("api_key_abandoned", key_id=key_id, claim=claim)

    @staticmethod
    def _new_key_row(name: str, scopes: list[str] | None) -> tuple[ApiKey, CreatedKey]:
        """Build an unsaved key row and the matching one-time result."""
        if scopes is None:
            scopes = ["memory:read", "memory:write", "admin"]
        prefix, secret, key_hash = _generate_key()
        key_id = f"key_{secrets.token_hex(8)}"
        now = get_utc_now()
        row = ApiKey(
            key_id=key_id,
            name=name,
            prefix=prefix,
            hash=key_hash,
            scopes=json.dumps(scopes),
            created_at=now,
            updated_at=now,
        )
        created = CreatedKey(
            key_id=key_id,
            name=name,
            prefix=prefix,
            secret=secret,
            scopes=scopes,
            created_at=now,
        )
        return row, created

    async def lookup_key(self, secret: str) -> ApiKey | None:
        """Look up a key by its secret (hashed for comparison).

        Returns None if not found or revoked.
        """
        key_hash = _hash_key(secret)
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApiKey).where(
                    ApiKey.hash == key_hash,
                    ApiKey.revoked_at.is_(None),
                )
            )
            return result.scalar_one_or_none()

    async def get_key_by_id(self, key_id: str) -> ApiKey | None:
        """Return a non-revoked key row by ``key_id`` (used by session auth)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApiKey).where(
                    ApiKey.key_id == key_id,
                    ApiKey.revoked_at.is_(None),
                )
            )
            return result.scalar_one_or_none()

    async def list_keys(self, include_revoked: bool = False) -> Sequence[KeyInfo]:
        """List all API keys (without secrets)."""
        async with self._session_factory() as session:
            stmt = select(ApiKey)
            if not include_revoked:
                stmt = stmt.where(ApiKey.revoked_at.is_(None))
            stmt = stmt.order_by(ApiKey.created_at.desc())
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return [
            KeyInfo(
                key_id=row.key_id,
                name=row.name,
                prefix=row.prefix,
                scopes=json.loads(row.scopes),
                created_at=row.created_at,
                updated_at=row.updated_at,
                last_used_at=row.last_used_at,
                revoked_at=row.revoked_at,
            )
            for row in rows
        ]

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke a key (soft delete). Returns True if found and revoked."""
        now = get_utc_now()
        async with self._session_factory() as session:
            result = await session.execute(
                update(ApiKey)
                .where(ApiKey.key_id == key_id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=now, updated_at=now)
            )
            await session.commit()
            revoked = result.rowcount > 0

        if revoked:
            logger.info("api_key_revoked", key_id=key_id)
        return revoked

    async def rotate_key(self, key_id: str) -> CreatedKey | None:
        """Rotate a key: generate new secret, invalidate old one.

        Returns the new key info or None if key_id not found.
        """
        prefix, secret, key_hash = _generate_key()
        now = get_utc_now()

        async with self._session_factory() as session:
            result = await session.execute(
                select(ApiKey).where(
                    ApiKey.key_id == key_id,
                    ApiKey.revoked_at.is_(None),
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None

            row.prefix = prefix
            row.hash = key_hash
            row.updated_at = now
            session.add(row)
            await session.commit()

            scopes = json.loads(row.scopes)

        logger.info("api_key_rotated", key_id=key_id)
        return CreatedKey(
            key_id=key_id,
            name=row.name,
            prefix=prefix,
            secret=secret,
            scopes=scopes,
            created_at=row.created_at,
        )

    async def touch_key(self, key_id: str) -> None:
        """Update a key's last_used_at timestamp."""
        now = get_utc_now()
        async with self._session_factory() as session:
            await session.execute(
                update(ApiKey).where(ApiKey.key_id == key_id).values(last_used_at=now)
            )
            await session.commit()

    async def key_count(self) -> int:
        """Count non-revoked API keys."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(ApiKey)
                .where(ApiKey.revoked_at.is_(None))
            )
            return result.scalar() or 0

    # ── Request Logs ────────────────────────────────────────────────────────

    async def log_request(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status: int,
        latency_ms: int,
        client: str | None = None,
        api_key_id: str | None = None,
        bytes_out: int | None = None,
    ) -> None:
        """Record a request in the audit log."""
        row = RequestLog(
            request_id=request_id,
            method=method,
            path=path,
            status=status,
            latency_ms=latency_ms,
            client=client,
            api_key_id=api_key_id,
            bytes_out=bytes_out,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

    async def list_request_logs(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        from_ts: dt.datetime | None = None,
        to_ts: dt.datetime | None = None,
        status: int | None = None,
        status_prefix: int | None = None,
        method: str | None = None,
        path: str | None = None,
        api_key_id: str | None = None,
    ) -> tuple[Sequence[RequestLog], int]:
        """List request logs with filters. Returns (items, total_count)."""
        async with self._session_factory() as session:
            stmt = select(RequestLog)
            count_stmt = select(func.count()).select_from(RequestLog)

            if from_ts is not None:
                stmt = stmt.where(RequestLog.ts >= from_ts)
                count_stmt = count_stmt.where(RequestLog.ts >= from_ts)
            if to_ts is not None:
                stmt = stmt.where(RequestLog.ts <= to_ts)
                count_stmt = count_stmt.where(RequestLog.ts <= to_ts)
            if status is not None:
                stmt = stmt.where(RequestLog.status == status)
                count_stmt = count_stmt.where(RequestLog.status == status)
            if status_prefix is not None:
                # "2" / "4xx" style filters: the whole hundred block.
                lower = status_prefix * 100
                stmt = stmt.where(
                    RequestLog.status >= lower, RequestLog.status < lower + 100
                )
                count_stmt = count_stmt.where(
                    RequestLog.status >= lower, RequestLog.status < lower + 100
                )
            if method is not None:
                stmt = stmt.where(RequestLog.method == method)
                count_stmt = count_stmt.where(RequestLog.method == method)
            if path is not None:
                stmt = stmt.where(RequestLog.path.startswith(path))
                count_stmt = count_stmt.where(RequestLog.path.startswith(path))
            if api_key_id is not None:
                stmt = stmt.where(RequestLog.api_key_id == api_key_id)
                count_stmt = count_stmt.where(RequestLog.api_key_id == api_key_id)

            stmt = stmt.order_by(RequestLog.ts.desc()).limit(limit).offset(offset)

            total_result = await session.execute(count_stmt)
            total = total_result.scalar() or 0

            result = await session.execute(stmt)
            items = result.scalars().all()

        return items, total

    async def get_request_log(self, log_id: int) -> RequestLog | None:
        """Get a single request log by ID."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(RequestLog).where(RequestLog.id == log_id)
            )
            return result.scalar_one_or_none()

    async def count_requests(
        self,
        *,
        from_ts: dt.datetime | None = None,
        path_suffix: str | None = None,
    ) -> int:
        """Count logged requests, optionally since ``from_ts`` and by suffix."""
        async with self._session_factory() as session:
            stmt = select(func.count()).select_from(RequestLog)
            if from_ts is not None:
                stmt = stmt.where(RequestLog.ts >= from_ts)
            if path_suffix is not None:
                stmt = stmt.where(RequestLog.path.like(f"%{path_suffix}"))
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def request_counts_by_hour(
        self, from_ts: dt.datetime, to_ts: dt.datetime
    ) -> Sequence[tuple[str, str, bool, int]]:
        """Count requests in ``[from_ts, to_ts)`` by UTC hour, path and 5xx.

        Returns ``(hour, path, is_5xx, count)`` rows; ``hour`` is
        ``YYYY-MM-DDTHH`` (UTC).
        """
        hour = func.strftime("%Y-%m-%dT%H", RequestLog.ts)
        is_error = RequestLog.status >= 500
        async with self._session_factory() as session:
            result = await session.execute(
                select(hour, RequestLog.path, is_error, func.count())
                .where(RequestLog.ts >= from_ts, RequestLog.ts < to_ts)
                .group_by(hour, RequestLog.path, is_error)
            )
            return [(h, p, bool(e), int(n)) for h, p, e, n in result.all()]

    async def prune_request_logs(self, retain_days: int) -> int:
        """Delete request logs older than retain_days. Returns count deleted."""
        cutoff = get_utc_now() - dt.timedelta(days=retain_days)
        async with self._session_factory() as session:
            result = await session.execute(
                text("DELETE FROM request_logs WHERE ts < :cutoff"),
                {"cutoff": cutoff},
            )
            await session.commit()
            deleted = result.rowcount

        if deleted > 0:
            logger.info("request_logs_pruned", deleted=deleted, retain_days=retain_days)
        return deleted

    # ── Usage Daily ─────────────────────────────────────────────────────────

    async def increment_usage(self, metric: str, delta: int = 1) -> None:
        """Increment a daily usage counter for today."""
        day = get_utc_now().strftime("%Y-%m-%d")
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO usage_daily (day, metric, value) "
                    "VALUES (:day, :metric, :delta) "
                    "ON CONFLICT(day, metric) DO UPDATE SET value = value + :delta"
                ),
                {"day": day, "metric": metric, "delta": delta},
            )
            await session.commit()

    async def get_usage(self, from_date: str, to_date: str) -> Sequence[UsageDaily]:
        """Get usage records for a date range (YYYY-MM-DD strings)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(UsageDaily)
                .where(UsageDaily.day >= from_date, UsageDaily.day <= to_date)
                .order_by(UsageDaily.day)
            )
            return result.scalars().all()

    # ── Feedback ────────────────────────────────────────────────────────────

    async def add_feedback(
        self,
        memory_id: str,
        rating: int,
        user_id: str | None = None,
        comment: str | None = None,
    ) -> int:
        """Add feedback on a memory. Returns the feedback ID."""
        row = Feedback(
            memory_id=memory_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id  # type: ignore[return-value]

    async def get_feedback(self, memory_id: str) -> Sequence[Feedback]:
        """Get all feedback for a memory, newest first."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Feedback)
                .where(Feedback.memory_id == memory_id)
                .order_by(Feedback.created_at.desc())
            )
            return result.scalars().all()

    # ── Tags ────────────────────────────────────────────────────────────────

    async def add_tags(
        self,
        memory_id: str,
        tags: list[str],
        user_id: str | None = None,
    ) -> int:
        """Add tags to a memory. Returns count of tags added."""
        now = get_utc_now()
        added = 0
        sql = (
            "INSERT OR IGNORE INTO tags "
            "(memory_id, tag, user_id, created_at) "
            "VALUES (:memory_id, :tag, :user_id, :created_at)"
        )
        async with self._session_factory() as session:
            for tag in tags:
                await session.execute(
                    text(sql),
                    {
                        "memory_id": memory_id,
                        "tag": tag,
                        "user_id": user_id,
                        "created_at": now,
                    },
                )
                added += 1
            await session.commit()
        return added

    async def remove_tags(self, memory_id: str, tags: list[str]) -> int:
        """Remove tags from a memory. Returns count removed."""
        if not tags:
            return 0
        async with self._session_factory() as session:
            stmt = select(Tag).where(Tag.memory_id == memory_id, Tag.tag.in_(tags))
            result = await session.execute(stmt)
            rows = result.scalars().all()
            removed = len(rows)
            for row in rows:
                await session.delete(row)
            await session.commit()
            return removed

    async def get_tags(self, memory_id: str) -> list[str]:
        """Get all tags for a memory."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Tag.tag).where(Tag.memory_id == memory_id)
            )
            return [row[0] for row in result.fetchall()]

    async def get_memories_by_tag(self, tag: str) -> list[str]:
        """Get all memory IDs with a specific tag."""
        async with self._session_factory() as session:
            result = await session.execute(select(Tag.memory_id).where(Tag.tag == tag))
            return [row[0] for row in result.fetchall()]
