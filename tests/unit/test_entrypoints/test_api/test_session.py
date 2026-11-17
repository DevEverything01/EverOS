"""Console session cookies: signing secret, expiry, key binding, cookie flags.

Pins: the secret is never a built-in constant (generated once and persisted
in ``control.db`` unless configured); a cookie carries ``iat``/``exp``/``kfp``
and stops working once expired or once its key is rotated; the ``Secure``
flag follows the request scheme (``X-Forwarded-Proto`` only when trusted).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from everos.config import AuthSettings, SqliteSettings
from everos.entrypoints.api import session as session_module
from everos.entrypoints.api.middleware.auth import AuthMiddleware, _is_session_path
from everos.entrypoints.api.session import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    SESSION_SECRET_SETTING,
    resolve_secret,
    sign_session,
    verify_session,
)
from everos.infra.persistence.sqlite import ControlStore

from .conftest import SecuredApp

SECRET = "test-session-secret"
HASH = "a" * 64
OLD_DEFAULT_SECRET = "everos-default-session-secret-change-me"
NOW = 1_800_000_000


class TestSessionToken:
    """Signing and verification of console session cookies."""

    def test_roundtrip_returns_claims(self) -> None:
        value = sign_session("key_abc", HASH, SECRET, now=NOW)

        claims = verify_session(value, SECRET, now=NOW + 1)

        assert claims is not None
        assert claims.key_id == "key_abc"
        assert claims.iat == NOW
        assert claims.exp == NOW + SESSION_MAX_AGE
        assert claims.matches_key(HASH) is True

    def test_payload_carries_iat_exp_kfp(self) -> None:
        payload = json.loads(sign_session("k", HASH, SECRET, now=NOW).rsplit(":", 1)[0])

        assert payload == {
            "key_id": "k",
            "iat": NOW,
            "exp": NOW + SESSION_MAX_AGE,
            "kfp": HASH[:16],
        }

    def test_expired_is_rejected(self) -> None:
        value = sign_session("key_abc", HASH, SECRET, now=NOW)

        assert verify_session(value, SECRET, now=NOW + SESSION_MAX_AGE) is None

    def test_other_key_hash_does_not_match(self) -> None:
        claims = verify_session(
            sign_session("k", HASH, SECRET, now=NOW), SECRET, now=NOW
        )

        assert claims is not None
        assert claims.matches_key("b" * 64) is False

    def test_wrong_secret_is_rejected(self) -> None:
        value = sign_session("key_abc", HASH, SECRET, now=NOW)

        assert verify_session(value, "other-secret", now=NOW) is None

    def test_tampered_payload_is_rejected(self) -> None:
        value = sign_session("key_abc", HASH, SECRET, now=NOW)
        payload, signature = value.rsplit(":", 1)

        forged = payload.replace("key_abc", "key_zzz") + ":" + signature
        assert verify_session(forged, SECRET, now=NOW) is None

    def test_legacy_cookie_without_exp_is_rejected(self) -> None:
        """The pre-fix format (``{"key_id": ...}`` only) no longer verifies."""
        import hashlib
        import hmac

        payload = json.dumps({"key_id": "key_abc"}, separators=(",", ":"))
        sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

        assert verify_session(f"{payload}:{sig}", SECRET, now=NOW) is None

    def test_garbage_is_rejected(self) -> None:
        assert verify_session("not-a-session", SECRET) is None


class TestSessionPaths:
    """The session endpoints authenticate from their own body."""

    @pytest.mark.parametrize(
        "path",
        ["/api/v2/auth/session", "/api/v2/auth/session/", "/api/v1/auth/session"],
    )
    def test_session_paths_detected(self, path: str) -> None:
        assert _is_session_path(path) is True

    def test_other_paths_are_not_session_paths(self) -> None:
        assert _is_session_path("/api/v2/auth/keys") is False


def _request(auth: AuthSettings, store: object = None) -> MagicMock:
    request = MagicMock()
    request.app.state = SimpleNamespace(auth_settings=auth, control_store=store)
    return request


class TestResolveSecret:
    """Where the signing secret comes from."""

    async def test_configured_secret_wins(self) -> None:
        auth = AuthSettings(session_secret=SecretStr("configured"))

        assert await resolve_secret(_request(auth)) == "configured"

    async def test_generated_secret_is_stable_and_not_the_old_constant(
        self, tmp_path
    ) -> None:
        store = ControlStore(tmp_path / "control.db", SqliteSettings())
        await store.create_schema()
        try:
            first = await resolve_secret(_request(AuthSettings(), store))
            second = await resolve_secret(_request(AuthSettings(), store))
        finally:
            await store.dispose()

        assert first == second
        assert first != OLD_DEFAULT_SECRET
        assert len(first) >= 40

    async def test_generated_secret_survives_restart(self, tmp_path) -> None:
        db = tmp_path / "control.db"
        store = ControlStore(db, SqliteSettings())
        await store.create_schema()
        first = await resolve_secret(_request(AuthSettings(), store))
        await store.dispose()

        reopened = ControlStore(db, SqliteSettings())
        await reopened.create_schema()
        try:
            again = await resolve_secret(_request(AuthSettings(), reopened))
            stored = await reopened.get_setting(SESSION_SECRET_SETTING)
        finally:
            await reopened.dispose()

        assert again == first == stored

    async def test_secrets_differ_between_installs(self, tmp_path) -> None:
        secrets = []
        for name in ("a", "b"):
            store = ControlStore(tmp_path / f"{name}.db", SqliteSettings())
            await store.create_schema()
            secrets.append(await resolve_secret(_request(AuthSettings(), store)))
            await store.dispose()

        assert secrets[0] != secrets[1]


class TestCookieAuthentication:
    """The middleware's cookie check uses the same verification."""

    async def test_valid_cookie_attaches_key_state(self) -> None:
        middleware = AuthMiddleware(MagicMock(), AuthSettings())
        request = _request(AuthSettings(session_secret=SecretStr(SECRET)))
        request.cookies = {SESSION_COOKIE_NAME: sign_session("key_abc", HASH, SECRET)}
        store = AsyncMock()
        store.get_key_by_id.return_value = SimpleNamespace(
            key_id="key_abc", scopes='["admin"]', hash=HASH
        )

        key_id = await middleware._session_key(request, store)

        assert key_id == "key_abc"
        assert request.state.scopes == ["admin"]

    async def test_rotated_key_rejected(self) -> None:
        middleware = AuthMiddleware(MagicMock(), AuthSettings())
        request = _request(AuthSettings(session_secret=SecretStr(SECRET)))
        request.cookies = {SESSION_COOKIE_NAME: sign_session("key_abc", HASH, SECRET)}
        store = AsyncMock()
        store.get_key_by_id.return_value = SimpleNamespace(
            key_id="key_abc", scopes='["admin"]', hash="b" * 64
        )

        assert await middleware._session_key(request, store) is None

    async def test_revoked_key_rejected(self) -> None:
        middleware = AuthMiddleware(MagicMock(), AuthSettings())
        request = _request(AuthSettings(session_secret=SecretStr(SECRET)))
        request.cookies = {SESSION_COOKIE_NAME: sign_session("key_gone", HASH, SECRET)}
        store = AsyncMock()
        store.get_key_by_id.return_value = None

        assert await middleware._session_key(request, store) is None

    async def test_no_cookie_returns_none(self) -> None:
        middleware = AuthMiddleware(MagicMock(), AuthSettings())
        request = MagicMock()
        request.cookies = {}

        assert await middleware._session_key(request, AsyncMock()) is None


async def _login(client, key: str):
    response = await client.post("/api/v2/auth/session", json={"key": key})
    assert response.status_code == 200
    return response


class TestSessionOverHttp:
    """End to end through the app with auth enforced."""

    async def test_fresh_cookie_authenticates(self, secured: SecuredApp) -> None:
        async with secured.client() as client:
            await _login(client, secured.admin_key)
            response = await client.get("/api/v2/admin/request-logs")

        assert response.status_code == 200

    async def test_no_cookie_is_401(self, secured: SecuredApp) -> None:
        async with secured.client() as client:
            response = await client.get("/api/v2/admin/request-logs")

        assert response.status_code == 401

    async def test_expired_cookie_is_401(
        self, secured: SecuredApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with secured.client() as client:
            await _login(client, secured.admin_key)
            later = session_module._now() + SESSION_MAX_AGE + 1
            monkeypatch.setattr(session_module, "_now", lambda: later)
            response = await client.get("/api/v2/admin/request-logs")

        assert response.status_code == 401

    async def test_cookie_dies_with_key_rotation(self, secured: SecuredApp) -> None:
        async with secured.client() as client:
            await _login(client, secured.admin_key)
            assert (await client.get("/api/v2/admin/request-logs")).status_code == 200

            await secured.store.rotate_key(secured.admin_key_id)
            response = await client.get("/api/v2/admin/request-logs")

        assert response.status_code == 401

    async def test_cookie_dies_with_key_revocation(self, secured: SecuredApp) -> None:
        async with secured.client() as client:
            await _login(client, secured.admin_key)
            await secured.store.revoke_key(secured.admin_key_id)
            response = await client.get("/api/v2/admin/request-logs")

        assert response.status_code == 401

    async def test_old_constant_secret_cannot_forge(self, secured: SecuredApp) -> None:
        key = await secured.store.lookup_key(secured.admin_key)
        forged = sign_session(key.key_id, key.hash, OLD_DEFAULT_SECRET)

        async with secured.client(cookies={SESSION_COOKIE_NAME: forged}) as client:
            response = await client.get("/api/v2/admin/request-logs")

        assert response.status_code == 401


class TestSecureFlag:
    """``Secure`` follows the scheme the client used."""

    @staticmethod
    def _cookie_header(response) -> str:
        return response.headers["set-cookie"].lower()

    async def test_plain_http_is_not_secure(self, secured: SecuredApp) -> None:
        async with secured.client() as client:
            response = await _login(client, secured.admin_key)

        assert "secure" not in self._cookie_header(response)

    async def test_https_sets_secure(self, secured: SecuredApp) -> None:
        async with secured.client(base_url="https://test") as client:
            response = await _login(client, secured.admin_key)
            logout = await client.delete("/api/v2/auth/session")

        assert "; secure" in self._cookie_header(response)
        assert "; secure" in self._cookie_header(logout)

    async def test_forwarded_proto_ignored_by_default(
        self, secured: SecuredApp
    ) -> None:
        async with secured.client(headers={"X-Forwarded-Proto": "https"}) as client:
            response = await _login(client, secured.admin_key)

        assert "secure" not in self._cookie_header(response)

    async def test_forwarded_proto_honoured_when_trusted(
        self, build_secured_app, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVEROS_AUTH__TRUST_FORWARDED_PROTO", "true")
        secured = await build_secured_app()

        async with secured.client(headers={"X-Forwarded-Proto": "https"}) as client:
            response = await _login(client, secured.admin_key)

        assert "; secure" in self._cookie_header(response)
