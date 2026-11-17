"""Bootstrap token: one admin key, ever, and its secret reaches the operator.

Before: the key was created on the first bootstrap request and its secret
thrown away (the bootstrap then stopped working, leaving no usable admin
key); the token compare was not constant-time; and "only once" was an
in-process flag, so concurrent requests could create several keys.
"""

from __future__ import annotations

import asyncio
import hmac
import stat
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from everos.config import load_settings
from everos.entrypoints.api.app import create_app
from everos.entrypoints.api.middleware.auth import (
    BOOTSTRAP_CLAIM,
    BOOTSTRAP_KEY_FILE,
    BOOTSTRAP_KEY_NAME,
)
from everos.infra.persistence.sqlite import ControlStore
from tests.fakes import make_application

TOKEN = "bootstrap-token-for-tests"


@pytest.fixture
def bootstrap_app(
    enforce_auth: None,
    control_store_db: ControlStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EVEROS_AUTH__BOOTSTRAP_TOKEN", TOKEN)
    load_settings.cache_clear()
    return create_app(
        application=make_application(root=tmp_path, control=control_store_db)
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bootstrap_keys(store: ControlStore) -> list:
    return [k for k in await store.list_keys() if k.name == BOOTSTRAP_KEY_NAME]


async def test_bootstrap_creates_one_key_and_saves_its_secret(
    bootstrap_app, control_store_db: ControlStore, tmp_path: Path
) -> None:
    async with _client(bootstrap_app) as client:
        response = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN}
        )

    assert response.status_code == 200
    keys = await _bootstrap_keys(control_store_db)
    assert len(keys) == 1
    key_file = tmp_path / BOOTSTRAP_KEY_FILE
    secret = key_file.read_text().strip()
    found = await control_store_db.lookup_key(secret)
    assert found is not None
    assert found.key_id == keys[0].key_id


async def test_key_file_is_owner_only(bootstrap_app, tmp_path: Path) -> None:
    async with _client(bootstrap_app) as client:
        await client.get("/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN})

    mode = stat.S_IMODE((tmp_path / BOOTSTRAP_KEY_FILE).stat().st_mode)
    assert mode == 0o600


async def test_saved_secret_authenticates_after_bootstrap(
    bootstrap_app, tmp_path: Path
) -> None:
    async with _client(bootstrap_app) as client:
        await client.get("/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN})
        secret = (tmp_path / BOOTSTRAP_KEY_FILE).read_text().strip()
        with_key = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": secret}
        )
        with_token_again = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN}
        )

    assert with_key.status_code == 200
    assert with_token_again.status_code == 401


async def test_concurrent_bootstrap_requests_create_one_key(
    bootstrap_app, control_store_db: ControlStore
) -> None:
    async with _client(bootstrap_app) as client:
        responses = await asyncio.gather(
            *(
                client.get("/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN})
                for _ in range(8)
            )
        )

    # The token is only valid while no key exists: requests that reach the
    # store after the winner committed are rejected like any unknown key.
    # Only the request that created the key is admitted by the token.
    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1
    assert statuses.count(401) == len(statuses) - 1
    assert len(await _bootstrap_keys(control_store_db)) == 1


async def test_create_key_once_has_a_single_winner(
    control_store_db: ControlStore,
) -> None:
    results = await asyncio.gather(
        *(control_store_db.create_key_once(BOOTSTRAP_CLAIM, "b") for _ in range(10))
    )

    assert sum(r is not None for r in results) == 1
    assert len(await control_store_db.list_keys()) == 1


async def test_claim_survives_a_new_process(
    control_store_db: ControlStore, tmp_path: Path
) -> None:
    from everos.config import SqliteSettings

    assert await control_store_db.create_key_once(BOOTSTRAP_CLAIM, "b") is not None
    other = ControlStore(tmp_path / "control.db", SqliteSettings())
    try:
        assert await other.create_key_once(BOOTSTRAP_CLAIM, "b") is None
    finally:
        await other.dispose()


async def test_token_compared_in_constant_time(
    bootstrap_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(
        "everos.entrypoints.api.middleware.auth.hmac.compare_digest", spy
    )
    async with _client(bootstrap_app) as client:
        response = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": "wrong-token"}
        )

    assert response.status_code == 401
    assert (b"wrong-token", TOKEN.encode()) in calls


async def test_wrong_token_creates_nothing(
    bootstrap_app, control_store_db: ControlStore, tmp_path: Path
) -> None:
    async with _client(bootstrap_app) as client:
        response = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN + "x"}
        )

    assert response.status_code == 401
    assert await control_store_db.list_keys() == []
    assert not (tmp_path / BOOTSTRAP_KEY_FILE).exists()


async def test_token_is_dead_after_every_key_is_revoked(
    bootstrap_app, control_store_db: ControlStore
) -> None:
    """Revoking all keys makes ``key_count() == 0`` again; the claim is still
    taken, so the token must not turn back into an admin credential."""
    async with _client(bootstrap_app) as client:
        first = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN}
        )
        other = await control_store_db.create_key("other", ["admin"])
        for key in await control_store_db.list_keys():
            await control_store_db.revoke_key(key.key_id)
        assert await control_store_db.key_count() == 0
        again = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN}
        )

    assert first.status_code == 200
    assert other.key_id
    assert again.status_code == 401
    everything = await control_store_db.list_keys(include_revoked=True)
    assert [k.name for k in everything].count(BOOTSTRAP_KEY_NAME) == 1
    assert await control_store_db.key_count() == 0


async def test_unwritable_secret_file_rolls_back_and_bootstrap_retries(
    bootstrap_app,
    control_store_db: ControlStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from everos.entrypoints.api.middleware import auth as auth_module

    real_write = auth_module._write_private

    def failing_write(path: Path, content: str) -> None:
        raise OSError(13, "Permission denied", str(path))

    monkeypatch.setattr(auth_module, "_write_private", failing_write)
    async with _client(bootstrap_app) as client:
        failed = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN}
        )

    assert failed.status_code == 401
    abandoned = await control_store_db.list_keys(include_revoked=True)
    assert len(abandoned) == 1
    assert abandoned[0].revoked_at is not None
    assert await control_store_db.key_count() == 0
    assert await control_store_db.get_setting(BOOTSTRAP_CLAIM) is None

    monkeypatch.setattr(auth_module, "_write_private", real_write)
    async with _client(bootstrap_app) as client:
        retried = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": TOKEN}
        )
        secret = (tmp_path / BOOTSTRAP_KEY_FILE).read_text().strip()
        with_key = await client.get(
            "/api/v2/admin/request-logs", headers={"X-Api-Key": secret}
        )

    assert retried.status_code == 200
    live = await control_store_db.lookup_key(secret)
    assert live is not None
    assert await control_store_db.get_setting(BOOTSTRAP_CLAIM) == live.key_id
    assert with_key.status_code == 200


async def test_abandon_only_releases_its_own_claim(
    control_store_db: ControlStore,
) -> None:
    created = await control_store_db.create_key_once(BOOTSTRAP_CLAIM, "b")
    assert created is not None

    await control_store_db.abandon_key_once(BOOTSTRAP_CLAIM, "key_someone_else")

    assert await control_store_db.get_setting(BOOTSTRAP_CLAIM) == created.key_id
    assert await control_store_db.key_count() == 1
