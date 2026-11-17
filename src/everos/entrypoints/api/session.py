"""Console session cookies: sign, verify and read.

The console authenticates with an API key once (``POST /api/v2/auth/session``)
and then carries a signed, httpOnly cookie. Both the route and the auth
middleware sign/verify through this module so the format cannot drift.

Cookie value: ``<json payload>:<hex hmac-sha256>``. The payload carries

- ``key_id`` -- the API key the session was opened with,
- ``iat`` / ``exp`` -- issue and expiry time (Unix seconds); ``exp`` is
  ``iat + SESSION_MAX_AGE`` and is enforced server-side, not only by the
  browser's ``max_age``,
- ``kfp`` -- the key fingerprint: the first 16 hex chars of the key's stored
  sha256 hash. Rotating the key changes its hash, so every session opened
  with the old secret stops verifying.

Signing secret: ``[auth] session_secret`` when configured. Otherwise a random
``secrets.token_urlsafe(32)`` is generated once and persisted in ``control.db``
(table ``control_settings``, key :data:`SESSION_SECRET_SETTING`), so it
survives restarts and is shared by every process on the same memory root.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException

from everos.core.utils.datetime import get_utc_now

if TYPE_CHECKING:
    from starlette.requests import Request

    from everos.config import AuthSettings

SESSION_COOKIE_NAME = "everos_session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60
SESSION_SECRET_SETTING = "auth.session_secret"
"""``control_settings`` key holding the generated session signing secret."""

KEY_FINGERPRINT_LEN = 16


@dataclass(frozen=True)
class SessionClaims:
    """The verified payload of a session cookie."""

    key_id: str
    iat: int
    exp: int
    kfp: str

    def matches_key(self, key_hash: str) -> bool:
        """True when the session was opened with the key's current secret."""
        return hmac.compare_digest(self.kfp, key_fingerprint(key_hash))


def key_fingerprint(key_hash: str) -> str:
    """The fingerprint of a key: its stored sha256 hash, truncated."""
    return key_hash[:KEY_FINGERPRINT_LEN]


def auth_settings(request: Request) -> AuthSettings | None:
    """The ``[auth]`` settings ``create_app`` installed on the app."""
    return getattr(request.app.state, "auth_settings", None)


async def resolve_secret(request: Request) -> str:
    """Return the session signing secret for this app.

    ``[auth] session_secret`` when configured; otherwise the generated
    secret persisted in ``control.db`` (created on first use). The value is
    cached on ``app.state`` after the first lookup.
    """
    settings = auth_settings(request)
    configured = getattr(settings, "session_secret", None)
    if configured is not None and configured.get_secret_value():
        return configured.get_secret_value()

    state = request.app.state
    cached = getattr(state, "session_secret", None)
    if isinstance(cached, str) and cached:
        return cached
    store = getattr(state, "control_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Control store not initialized")
    secret = await store.get_or_create_setting(
        SESSION_SECRET_SETTING, secrets.token_urlsafe(32)
    )
    state.session_secret = secret
    return secret


def _now() -> int:
    return int(get_utc_now().timestamp())


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_session(
    api_key_id: str, key_hash: str, secret: str, *, now: int | None = None
) -> str:
    """Return a signed session value for ``api_key_id``.

    Args:
        api_key_id: The key the session belongs to.
        key_hash: The key's stored sha256 hash (fingerprinted into ``kfp``).
        secret: The signing secret (:func:`resolve_secret`).
        now: Issue time in Unix seconds; defaults to the current time.
    """
    iat = _now() if now is None else now
    payload = json.dumps(
        {
            "key_id": api_key_id,
            "iat": iat,
            "exp": iat + SESSION_MAX_AGE,
            "kfp": key_fingerprint(key_hash),
        },
        separators=(",", ":"),
    )
    return f"{payload}:{_sign(payload, secret)}"


def verify_session(
    session_value: str, secret: str, *, now: int | None = None
) -> SessionClaims | None:
    """Return the claims of a validly signed, unexpired session, else ``None``.

    The caller must still check :meth:`SessionClaims.matches_key` against the
    key row it loads for ``key_id``.
    """
    if ":" not in session_value:
        return None
    payload, signature = session_value.rsplit(":", 1)
    if not hmac.compare_digest(_sign(payload, secret), signature):
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    key_id, iat, exp, kfp = (data.get(k) for k in ("key_id", "iat", "exp", "kfp"))
    if not (isinstance(key_id, str) and key_id and isinstance(kfp, str) and kfp):
        return None
    if not (isinstance(iat, int) and isinstance(exp, int)):
        return None
    if (_now() if now is None else now) >= exp:
        return None
    return SessionClaims(key_id=key_id, iat=iat, exp=exp, kfp=kfp)


def request_is_https(request: Request) -> bool:
    """Whether the client reached us over HTTPS (cookie ``Secure`` flag).

    ``X-Forwarded-Proto`` is honoured only with
    ``[auth] trust_forwarded_proto = true`` (behind a TLS-terminating proxy);
    otherwise a client could not be trusted to set it.
    """
    if request.url.scheme == "https":
        return True
    settings = auth_settings(request)
    if settings is None or not settings.trust_forwarded_proto:
        return False
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.split(",")[0].strip().lower() == "https"
