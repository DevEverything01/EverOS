"""FastAPI dependencies: the application, request id, auth, and control store.

Routes declare them in their signature through ``Depends`` rather than
calling them on the ``Request``, so a test can swap the
:class:`Application` with ``app.dependency_overrides[application]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request

from everos.core.observability.tracing import gen_request_id
from everos.service import Application

if TYPE_CHECKING:
    from everos.service.control import ControlStore


def extract_request_id(request: Request) -> str:
    """Return the request_id set by middleware, or mint a fresh fallback."""
    rid = getattr(request.state, "request_id", None)
    return str(rid) if rid else gen_request_id()


def application(request: Request) -> Application:
    """The :class:`Application` the app's lifespan (or ``create_app``) installed."""
    return request.app.state.application


def control_store(request: Request) -> ControlStore:
    """The :class:`ControlStore` for auth and control-plane operations."""
    store = getattr(request.app.state, "control_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Control store not initialized")
    return store


def api_key_id(request: Request) -> str | None:
    """The API key ID set by auth middleware, or None if unauthenticated."""
    return getattr(request.state, "api_key_id", None)


def scopes(request: Request) -> list[str]:
    """The scopes of the authenticated API key, or empty if unauthenticated."""
    return getattr(request.state, "scopes", [])


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=403, detail=message)


def _auth_open(request: Request) -> bool:
    """True when the auth middleware marked this request as unauthenticated-open.

    Only :class:`AuthMiddleware` sets the flag, and only when
    ``[auth]`` is not enforced (``enabled=false`` or ``legacy_open=true``).
    Anything else -- including a request that never passed the middleware --
    is checked against the key's scopes (fail closed).
    """
    return getattr(request.state, "auth_open", False) is True


def require_admin(request: Request) -> None:
    """Dependency that requires the ``admin`` scope."""
    if _auth_open(request):
        return
    if "admin" not in scopes(request):
        raise _forbidden("Admin scope required")


def require_read(request: Request) -> None:
    """Dependency that requires ``memory:read`` (``admin`` implies it).

    ``memory:write`` does not: a write-only key (an ingestion pipeline) must
    not be able to read back every user's memory.
    """
    if _auth_open(request):
        return
    granted = scopes(request)
    if "memory:read" not in granted and "admin" not in granted:
        raise _forbidden("memory:read scope required")


def require_write(request: Request) -> None:
    """Dependency that requires ``memory:write`` (``admin`` implies it)."""
    if _auth_open(request):
        return
    granted = scopes(request)
    if "memory:write" not in granted and "admin" not in granted:
        raise _forbidden("memory:write scope required")


ApplicationDep = Annotated[Application, Depends(application)]
"""Route parameter type for the installed :class:`Application`."""

RequestIdDep = Annotated[str, Depends(extract_request_id)]
"""Route parameter type for the request id."""

ControlStoreDep = Annotated["ControlStore", Depends(control_store)]
"""Route parameter type for the :class:`ControlStore`."""

ApiKeyIdDep = Annotated[str | None, Depends(api_key_id)]
"""Route parameter type for the authenticated API key ID."""

ScopesDep = Annotated[list[str], Depends(scopes)]
"""Route parameter type for the authenticated scopes."""

RequireAdminDep = Annotated[None, Depends(require_admin)]
"""Dependency that ensures admin scope."""

RequireReadDep = Annotated[None, Depends(require_read)]
"""Dependency that ensures ``memory:read`` scope."""

RequireWriteDep = Annotated[None, Depends(require_write)]
"""Dependency that ensures ``memory:write`` scope."""
