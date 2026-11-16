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
configured, requests with that token are accepted. On the first such request,
an admin key is created automatically.

The middleware lazily obtains the :class:`ControlStore` from ``app.state``
so that it can be instantiated before the lifespan runs.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from everos.config import AuthSettings
from everos.core.observability.logging import get_logger
from everos.entrypoints.api.session import (
    SESSION_COOKIE_NAME,
    resolve_secret,
    verify_session,
)

if TYPE_CHECKING:
    from everos.service.control import ControlStore

logger = get_logger(__name__)

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
        self._bootstrap_used = False

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

        await store.touch_key(api_key.key_id)

        return await call_next(request)

    async def _session_key(self, request: Request, store: ControlStore) -> str | None:
        """Authenticate a console session cookie; attach state when valid."""
        raw = request.cookies.get(SESSION_COOKIE_NAME)
        if not raw:
            return None
        key_id = verify_session(raw, resolve_secret(request))
        if key_id is None:
            return None
        api_key = await store.get_key_by_id(key_id)
        if api_key is None:
            logger.warning(
                "auth_rejected", path=request.url.path, reason="session_key_revoked"
            )
            return None
        request.state.api_key_id = api_key.key_id
        request.state.scopes = json.loads(api_key.scopes)
        await store.touch_key(api_key.key_id)
        return api_key.key_id

    async def _try_bootstrap(
        self, token: str, request: Request, store: ControlStore
    ) -> bool:
        """Handle bootstrap token: create first admin key if conditions met.

        Returns True if the request should proceed (bootstrap successful),
        False if bootstrap does not apply and normal auth should continue.
        """
        bootstrap_token = self._settings.bootstrap_token
        if bootstrap_token is None:
            return False
        if token != bootstrap_token.get_secret_value():
            return False

        key_count = await store.key_count()
        if key_count > 0:
            return False

        if not self._bootstrap_used:
            key = await store.create_key(
                "bootstrap-admin",
                scopes=["memory:read", "memory:write", "admin"],
            )
            logger.info(
                "bootstrap_key_created",
                key_id=key.key_id,
                prefix=key.prefix,
            )
            self._bootstrap_used = True

        request.state.api_key_id = "bootstrap"
        request.state.scopes = ["memory:read", "memory:write", "admin"]
        return True


def require_scope(scopes: list[str], required: str) -> bool:
    """Check if required scope is present in the list."""
    return required in scopes or "admin" in scopes


def require_any_scope(scopes: list[str], required: list[str]) -> bool:
    """Check if any of the required scopes is present."""
    if "admin" in scopes:
        return True
    return any(s in scopes for s in required)
