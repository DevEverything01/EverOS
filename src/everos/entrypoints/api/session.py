"""Console session cookies: sign, verify and read.

The console authenticates with an API key once (``POST /api/v2/auth/session``)
and then carries a signed, httpOnly cookie. Both the route and the auth
middleware sign/verify through this module so the format cannot drift.
"""

from __future__ import annotations

import hashlib
import hmac
import json

DEFAULT_SESSION_SECRET = "everos-default-session-secret-change-me"

SESSION_COOKIE_NAME = "everos_session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60


def resolve_secret(request) -> str:
    """Return the session signing secret for this app.

    Uses ``[auth] session_secret`` when configured; otherwise the built-in
    default, so a console session stays valid for the life of the process.
    """
    app = getattr(request.app.state, "application", None)
    settings = getattr(app, "_settings", None) if app is not None else None
    auth = getattr(settings, "auth", None)
    secret = getattr(auth, "session_secret", None)
    if secret:
        return secret.get_secret_value()
    return DEFAULT_SESSION_SECRET


def sign_session(api_key_id: str, secret: str) -> str:
    """Return a signed session value for ``api_key_id``."""
    payload = json.dumps({"key_id": api_key_id}, separators=(",", ":"))
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_session(session_value: str, secret: str) -> str | None:
    """Return the ``key_id`` of a valid session value, else ``None``."""
    if ":" not in session_value:
        return None
    payload, signature = session_value.rsplit(":", 1)
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    key_id = data.get("key_id")
    return key_id if isinstance(key_id, str) and key_id else None
