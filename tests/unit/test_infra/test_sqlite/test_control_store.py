"""Unit tests for the control-plane storage (ControlStore)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from everos.config import SqliteSettings
from everos.infra.persistence.sqlite import ControlStore


@pytest.fixture
async def store(tmp_path: Path) -> ControlStore:
    """Create a temporary ControlStore for testing."""
    db_path = tmp_path / "control.db"
    settings = SqliteSettings()
    store = ControlStore(db_path, settings)
    await store.create_schema()
    yield store
    await store.dispose()


class TestApiKeys:
    """Tests for API key management."""

    async def test_create_key(self, store: ControlStore) -> None:
        """Creating a key returns the full secret and stores the hash."""
        key = await store.create_key("test-key")

        assert key.key_id.startswith("key_")
        assert key.name == "test-key"
        assert key.secret.startswith("eos_")
        assert key.prefix == key.secret[4:12]
        assert "memory:read" in key.scopes
        assert "memory:write" in key.scopes
        assert "admin" in key.scopes

    async def test_create_key_custom_scopes(self, store: ControlStore) -> None:
        """Creating a key with custom scopes respects them."""
        key = await store.create_key("read-only", scopes=["memory:read"])

        assert key.scopes == ["memory:read"]

    async def test_lookup_key_valid(self, store: ControlStore) -> None:
        """Looking up a valid key by secret returns the key."""
        created = await store.create_key("lookup-test")

        found = await store.lookup_key(created.secret)

        assert found is not None
        assert found.key_id == created.key_id
        assert found.name == "lookup-test"

    async def test_lookup_key_invalid(self, store: ControlStore) -> None:
        """Looking up an invalid secret returns None."""
        await store.create_key("some-key")

        found = await store.lookup_key("eos_invalid_secret")

        assert found is None

    async def test_lookup_key_revoked(self, store: ControlStore) -> None:
        """Looking up a revoked key returns None."""
        created = await store.create_key("to-revoke")
        await store.revoke_key(created.key_id)

        found = await store.lookup_key(created.secret)

        assert found is None

    async def test_list_keys(self, store: ControlStore) -> None:
        """Listing keys returns all non-revoked keys without secrets."""
        k1 = await store.create_key("key-1")
        k2 = await store.create_key("key-2")
        await store.revoke_key(k1.key_id)

        keys = await store.list_keys()

        assert len(keys) == 1
        assert keys[0].key_id == k2.key_id
        assert keys[0].name == "key-2"

    async def test_revoke_key(self, store: ControlStore) -> None:
        """Revoking a key marks it as revoked."""
        created = await store.create_key("to-revoke")

        result = await store.revoke_key(created.key_id)

        assert result is True
        assert await store.lookup_key(created.secret) is None

    async def test_revoke_key_not_found(self, store: ControlStore) -> None:
        """Revoking a non-existent key returns False."""
        result = await store.revoke_key("key_nonexistent")

        assert result is False

    async def test_rotate_key(self, store: ControlStore) -> None:
        """Rotating a key generates a new secret."""
        created = await store.create_key("to-rotate")
        old_secret = created.secret

        rotated = await store.rotate_key(created.key_id)

        assert rotated is not None
        assert rotated.key_id == created.key_id
        assert rotated.secret != old_secret
        assert rotated.secret.startswith("eos_")
        assert await store.lookup_key(old_secret) is None
        assert await store.lookup_key(rotated.secret) is not None

    async def test_rotate_key_not_found(self, store: ControlStore) -> None:
        """Rotating a non-existent key returns None."""
        result = await store.rotate_key("key_nonexistent")

        assert result is None

    async def test_touch_key(self, store: ControlStore) -> None:
        """Touching a key updates last_used_at."""
        created = await store.create_key("to-touch")

        await store.touch_key(created.key_id)

        keys = await store.list_keys()
        assert len(keys) == 1
        assert keys[0].last_used_at is not None

    async def test_key_count(self, store: ControlStore) -> None:
        """Counting keys returns the number of non-revoked keys."""
        assert await store.key_count() == 0

        k1 = await store.create_key("key-1")
        assert await store.key_count() == 1

        await store.create_key("key-2")
        assert await store.key_count() == 2

        await store.revoke_key(k1.key_id)
        assert await store.key_count() == 1


class TestRequestLogs:
    """Tests for request logging."""

    async def test_log_request(self, store: ControlStore) -> None:
        """Logging a request stores it."""
        await store.log_request(
            request_id="req-1",
            method="POST",
            path="/api/v2/memory/add",
            status=200,
            latency_ms=150,
            client="127.0.0.1",
            api_key_id="key_123",
        )

        logs, total = await store.list_request_logs()

        assert total == 1
        assert len(logs) == 1
        assert logs[0].request_id == "req-1"
        assert logs[0].method == "POST"
        assert logs[0].status == 200

    async def test_list_request_logs_filters(self, store: ControlStore) -> None:
        """Listing request logs respects filters."""
        await store.log_request(
            request_id="req-1",
            method="GET",
            path="/api/v2/memory/get",
            status=200,
            latency_ms=50,
        )
        await store.log_request(
            request_id="req-2",
            method="POST",
            path="/api/v2/memory/add",
            status=500,
            latency_ms=100,
        )

        logs, _ = await store.list_request_logs(status=500)
        assert len(logs) == 1
        assert logs[0].request_id == "req-2"

        logs, _ = await store.list_request_logs(method="GET")
        assert len(logs) == 1
        assert logs[0].request_id == "req-1"

    async def test_prune_request_logs(self, store: ControlStore) -> None:
        """Pruning removes old logs."""
        await store.log_request(
            request_id="req-1",
            method="GET",
            path="/api/v2/memory/get",
            status=200,
            latency_ms=50,
        )

        deleted = await store.prune_request_logs(retain_days=0)

        assert deleted == 1
        logs, _ = await store.list_request_logs()
        assert len(logs) == 0


class TestUsageDaily:
    """Tests for daily usage tracking."""

    async def test_increment_usage(self, store: ControlStore) -> None:
        """Incrementing usage creates or updates counters."""
        await store.increment_usage("add_calls", 1)
        await store.increment_usage("add_calls", 2)
        await store.increment_usage("search_calls", 1)

        from everos.core.utils.datetime import get_utc_now

        today = get_utc_now().strftime("%Y-%m-%d")
        usage = await store.get_usage(today, today)

        metrics = {u.metric: u.value for u in usage}
        assert metrics["add_calls"] == 3
        assert metrics["search_calls"] == 1


class TestFeedback:
    """Tests for feedback storage."""

    async def test_add_feedback(self, store: ControlStore) -> None:
        """Adding feedback stores it."""
        feedback_id = await store.add_feedback(
            memory_id="mem-1",
            rating=1,
            user_id="user-1",
            comment="Great!",
        )

        assert feedback_id > 0


class TestTags:
    """Tests for memory tagging."""

    async def test_add_and_get_tags(self, store: ControlStore) -> None:
        """Adding and getting tags works."""
        await store.add_tags("mem-1", ["tag1", "tag2"], user_id="user-1")

        tags = await store.get_tags("mem-1")

        assert set(tags) == {"tag1", "tag2"}

    async def test_get_memories_by_tag(self, store: ControlStore) -> None:
        """Getting memories by tag works."""
        await store.add_tags("mem-1", ["shared"])
        await store.add_tags("mem-2", ["shared"])
        await store.add_tags("mem-3", ["other"])

        memories = await store.get_memories_by_tag("shared")

        assert set(memories) == {"mem-1", "mem-2"}


def _db_file_modes(directory: Path) -> dict[str, int]:
    return {
        p.name: stat.S_IMODE(p.stat().st_mode)
        for p in directory.iterdir()
        if p.name.startswith("control.db")
    }


def _chmod_all(directory: Path, mode: int) -> None:
    for p in directory.iterdir():
        p.chmod(mode)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
async def test_control_db_and_sidecars_are_owner_only(tmp_path: Path) -> None:
    """``control.db`` holds the session secret and key hashes: 0600, sidecars too."""
    old = os.umask(0o022)
    try:
        store = ControlStore(tmp_path / "control.db", SqliteSettings())
        await store.create_schema()
        await store.create_key("k", ["admin"])
        modes = _db_file_modes(tmp_path)
        await store.dispose()
    finally:
        os.umask(old)

    assert "control.db" in modes
    assert modes == dict.fromkeys(modes, 0o600)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
async def test_existing_world_readable_db_is_tightened(tmp_path: Path) -> None:
    db = tmp_path / "control.db"
    first = ControlStore(db, SqliteSettings())
    await first.create_schema()
    await first.dispose()
    _chmod_all(tmp_path, 0o644)

    reopened = ControlStore(db, SqliteSettings())
    await reopened.create_schema()
    try:
        modes = _db_file_modes(tmp_path)
        assert "control.db" in modes
        assert modes == dict.fromkeys(modes, 0o600)
    finally:
        await reopened.dispose()
