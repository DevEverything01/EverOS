"""API authentication middleware.

Enforces Bearer/X-Api-Key authentication for the HTTP API:

- Extracts the token from ``Authorization: Bearer <key>`` or ``X-Api-Key: <key>``
- Looks up the key by its sha256 hash in the control store
- Rejects revoked or unknown keys with HTTP 401
- Attaches ``api_key_id`` and ``scopes`` to ``request.state`` on success
- Skips authentication for paths in the exempt list

The middleware is only active when ``settings.auth.auth_required`` is True
(``enabled=True`` and ``legacy_open=False``).

Bootstrap token handling: if no API keys exist and a bootstrap token is
configured, requests with that token (compared in constant time) are
accepted. The first such request creates the ``bootstrap-admin`` key exactly
once -- guarded by a claim row in ``control.db``, so concurrent requests and
processes cannot create two -- and writes its secret to
``<memory root>/bootstrap-admin.key`` (mode 0600), the only place it is ever
shown. If that file cannot be written the key is revoked and the claim
released, so a later bootstrap can retry instead of locking the operator out.
Once the claim is taken the token grants nothing, even after every key is
revoked.

A session cookie authenticates an unsafe request (POST / PUT / PATCH /
DELETE) only when its ``Origin`` / ``Referer`` is this server
(:mod:`everos.entrypoints.api.same_origin`); token-authenticated requests are
not checked, since a browser never adds those headers by itself.

When auth is not enforced the middleware marks the request
``request.state.auth_open = True``; scope dependencies honour only that flag.

The middleware lazily obtains the :class:`ControlStore` from ``app.state``
so that it can be instantiated before the lifespan runs.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from everos.config import AuthSettings
from everos.core.observability.logging import get_logger
from everos.entrypoints.api.same_origin import UNSAFE_METHODS, is_cross_site
from everos.entrypoints.api.session import (
    SESSION_COOKIE_NAME,
    resolve_secret,
    verify_session,
)

if TYPE_CHECKING:
    from everos.service.control import ControlStore

logger = get_logger(__name__)

BOOTSTRAP_KEY_NAME = "bootstrap-admin"
BOOTSTRAP_KEY_FILE = "bootstrap-admin.key"
BOOTSTRAP_CLAIM = "auth.bootstrap_admin_key_id"
"""``control_settings`` row claimed when the bootstrap key is created."""
TOUCH_INTERVAL_S = 60.0
"""Minimum seconds between two ``last_used_at`` writes for one key."""
_ADMIN_SCOPES = ["memory:read", "memory:write", "admin"]

_AUTH_HEADER = "Authorization"
_API_KEY_HEADER = "X-Api-Key"
_BEARER_PREFIX = "Bearer "


def _error_response(code: str, message: str, status: int = 401) -> JSONResponse:
    """Return a Cloud-style error envelope."""
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
    )


def _is_session_path(path: str) -> bool:
    """True for the console session endpoints, which validate their own body."""
    return path.rstrip("/").endswith("/auth/session")


def _is_exempt(path: str, exempt_paths: list[str]) -> bool:
    """Check if path matches any exempt prefix."""
    for exempt in exempt_paths:
        if path == exempt or path.startswith(exempt + "/"):
            return True
    return False


def _extract_token(request: Request) -> str | None:
    """Extract token from Authorization header or X-Api-Key header."""
    auth_header = request.headers.get(_AUTH_HEADER)
    if auth_header and auth_header.startswith(_BEARER_PREFIX):
        return auth_header[len(_BEARER_PREFIX) :]

    api_key_header = request.headers.get(_API_KEY_HEADER)
    if api_key_header:
        return api_key_header

    return None


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware for the HTTP API.

    The middleware lazily gets the :class:`ControlStore` from
    ``request.app.state.control_store``. If auth is required but the store
    is not available, requests to protected paths fail with 503.

    Args:
        app: The ASGI application to wrap.
        settings: Auth configuration from ``[server.auth]``.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[Response]],
        settings: AuthSettings,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._touched: dict[str, float] = {}

    async def _touch(self, store: ControlStore, key_id: str) -> None:
        """Record key use, at most once per key per ``TOUCH_INTERVAL_S``.

        ``last_used_at`` is a coarse "seen recently" marker; writing it on
        every request would put a SQLite write on each request's hot path.
        The throttle runs on the monotonic clock (immune to wall-clock jumps);
        a failed write does not count, so the next request retries it.
        """
        now = time.monotonic()
        last = self._touched.get(key_id)
        if last is not None and now - last < TOUCH_INTERVAL_S:
            return
        self._touched[key_id] = now
        try:
            await store.touch_key(key_id)
        except Exception:
            # Roll the throttle back so the next request retries the write.
            if last is None:
                self._touched.pop(key_id, None)
            else:
                self._touched[key_id] = last
            logger.warning("api_key_touch_failed", key_id=key_id, exc_info=True)

    def _get_store(self, request: Request) -> ControlStore | None:
        """Get the control store from app state, or None if not available."""
        return getattr(request.app.state, "control_store", None)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        if not self._settings.auth_required:
            request.state.auth_open = True
            return await call_next(request)

        if _is_exempt(path, self._settings.exempt_paths):
            return await call_next(request)

        if not path.startswith("/api/"):
            return await call_next(request)

        # Session endpoints authenticate from the request body themselves.
        if _is_session_path(path):
            return await call_next(request)

        store = self._get_store(request)
        if store is None:
            return _error_response(
                "service_unavailable",
                "Auth service not initialized",
                status=503,
            )

        token = _extract_token(request)
        if token is None:
            session_key = await self._session_key(request, store)
            if session_key is not None:
                # The cookie rides along on any browser request, including a
                # cross-site form post; an API-key header never does.
                if request.method in UNSAFE_METHODS and is_cross_site(request):
                    logger.warning(
                        "auth_rejected", path=path, reason="session_cross_site"
                    )
                    return _error_response(
                        "CROSS_SITE_REFUSED",
                        "A session-authenticated write must come from this "
                        "server's own pages; use an API key header elsewhere.",
                        status=403,
                    )
                return await call_next(request)
            return _error_response(
                "unauthorized",
                "Missing API key. Use Authorization: Bearer <key> or X-Api-Key.",
            )

        if await self._try_bootstrap(token, request, store):
            return await call_next(request)

        api_key = await store.lookup_key(token)
        if api_key is None:
            logger.warning("auth_rejected", path=path, reason="invalid_or_revoked")
            return _error_response(
                "unauthorized",
                "Invalid or revoked API key.",
            )

        request.state.api_key_id = api_key.key_id
        request.state.scopes = json.loads(api_key.scopes)

        await self._touch(store, api_key.key_id)

        return await call_next(request)

    async def _session_key(self, request: Request, store: ControlStore) -> str | None:
        """Authenticate a console session cookie; attach state when valid."""
        raw = request.cookies.get(SESSION_COOKIE_NAME)
        if not raw:
            return None
        claims = verify_session(raw, await resolve_secret(request))
        if claims is None:
            logger.warning(
                "auth_rejected", path=request.url.path, reason="session_invalid"
            )
            return None
        api_key = await store.get_key_by_id(claims.key_id)
        if api_key is None:
            logger.warning(
                "auth_rejected", path=request.url.path, reason="session_key_revoked"
            )
            return None
        if not claims.matches_key(api_key.hash):
            logger.warning(
                "auth_rejected", path=request.url.path, reason="session_key_rotated"
            )
            return None
        request.state.api_key_id = api_key.key_id
        request.state.scopes = json.loads(api_key.scopes)
        await self._touch(store, api_key.key_id)
        return api_key.key_id

    async def _try_bootstrap(
        self, token: str, request: Request, store: ControlStore
    ) -> bool:
        """Handle bootstrap token: create first admin key if conditions met.

        Returns True only when this request created the bootstrap key and its
        secret reached the key file; the request then proceeds as admin.
        False otherwise (token mismatch, a live key exists, the claim was
        already taken, or the secret could not be saved), and normal auth
        continues -- so a bootstrap token is never a standing admin backdoor.
        """
        bootstrap_token = self._settings.bootstrap_token
        if bootstrap_token is None:
            return False
        if not hmac.compare_digest(
            token.encode(), bootstrap_token.get_secret_value().encode()
        ):
            return False

        key_count = await store.key_count()
        if key_count > 0:
            return False

        key = await store.create_key_once(
            BOOTSTRAP_CLAIM, BOOTSTRAP_KEY_NAME, scopes=list(_ADMIN_SCOPES)
        )
        if key is None:
            # The claim was taken before: by a concurrent bootstrap, or by one
            # whose key has since been revoked. Either way the token grants
            # nothing any more; the request is judged like any other token.
            return False

        path = await self._save_bootstrap_secret(request, key.secret)
        if path is None:
            # Nobody can ever learn the secret, and with a live key the token
            # stops working: undo, so the next bootstrap request can retry.
            await store.abandon_key_once(BOOTSTRAP_CLAIM, key.key_id)
            logger.error(
                "bootstrap_key_abandoned",
                key_id=key.key_id,
                reason="secret_file_unwritable",
                hint="fix the memory root's permissions and retry the bootstrap",
            )
            return False
        logger.warning(
            "bootstrap_key_created",
            key_id=key.key_id,
            prefix=key.prefix,
            secret_file=str(path),
        )

        request.state.api_key_id = "bootstrap"
        request.state.scopes = list(_ADMIN_SCOPES)
        return True

    async def _save_bootstrap_secret(
        self, request: Request, secret: str
    ) -> Path | None:
        """Write the bootstrap key's secret to ``<root>/bootstrap-admin.key`` (0600)."""
        application = getattr(request.app.state, "application", None)
        root = getattr(getattr(application, "_root", None), "root", None)
        if not isinstance(root, str | os.PathLike):
            logger.error("bootstrap_key_secret_unsaved", reason="no_memory_root")
            return None
        path = Path(root) / BOOTSTRAP_KEY_FILE
        try:
            await anyio.to_thread.run_sync(_write_private, path, secret + "\n")
        except OSError:
            logger.error("bootstrap_key_secret_unsaved", path=str(path), exc_info=True)
            return None
        return path


def _write_private(path: Path, content: str) -> None:
    """Create/overwrite ``path`` with mode 0600 from the first byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):  # POSIX: also fix a pre-existing file's mode
            os.fchmod(fd, 0o600)
        os.write(fd, content.encode())
    finally:
        os.close(fd)


def require_scope(scopes: list[str], required: str) -> bool:
    """Check if required scope is present in the list."""
    return required in scopes or "admin" in scopes


def require_any_scope(scopes: list[str], required: list[str]) -> bool:
    """Check if any of the required scopes is present."""
    if "admin" in scopes:
        return True
    return any(s in scopes for s in required)
