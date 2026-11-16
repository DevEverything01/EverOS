"""API-key management and session endpoints.

Endpoints:
- POST /auth/keys - create a new API key
- GET /auth/keys - list all API keys (without secrets)
- DELETE /auth/keys/{key_id} - revoke a key (soft delete)
- POST /auth/keys/{key_id}/rotate - rotate a key (new secret)
- POST /auth/session - set session cookie (for console)
- DELETE /auth/session - clear session cookie

All key-management endpoints require ``admin`` scope.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from everos.core.utils.datetime import to_iso_format
from everos.entrypoints.api.dependencies import (
    ControlStoreDep,
    RequestIdDep,
    RequireAdminDep,
)

router = APIRouter(tags=["auth"])

SESSION_COOKIE_NAME = "everos_session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60


class CreateKeyRequest(BaseModel):
    """Request body for creating an API key."""

    name: str = Field(..., min_length=1, max_length=128)
    scopes: list[str] | None = Field(default=None)


class CreateKeyResponse(BaseModel):
    """Response for creating an API key (includes secret once)."""

    key_id: str
    name: str
    key: str
    prefix: str
    scopes: list[str]
    created_at: str


class KeyListItem(BaseModel):
    """A single key in the list response (no secret)."""

    key_id: str
    name: str
    prefix: str
    scopes: list[str]
    created_at: str
    updated_at: str
    last_used_at: str | None
    revoked_at: str | None


class KeyListResponse(BaseModel):
    """Response for listing API keys."""

    keys: list[KeyListItem]


class SessionRequest(BaseModel):
    """Request body for creating a session."""

    key: str = Field(..., min_length=1)


class SessionResponse(BaseModel):
    """Response for session creation."""

    ok: bool
    message: str


def _require_admin() -> RequireAdminDep:
    """Dependency marker for admin scope."""
    pass


@router.post(
    "/auth/keys",
    response_model=CreateKeyResponse,
    dependencies=[Depends(_require_admin)],
)
async def create_key(
    body: CreateKeyRequest,
    store: ControlStoreDep,
    request_id: RequestIdDep,
) -> CreateKeyResponse:
    """Create a new API key.

    The ``key`` field contains the full secret and is only returned once.
    Store it securely; it cannot be retrieved later.
    """
    key = await store.create_key(body.name, body.scopes)
    return CreateKeyResponse(
        key_id=key.key_id,
        name=key.name,
        key=key.secret,
        prefix=key.prefix,
        scopes=key.scopes,
        created_at=to_iso_format(key.created_at),
    )


@router.get(
    "/auth/keys",
    response_model=KeyListResponse,
    dependencies=[Depends(_require_admin)],
)
async def list_keys(
    store: ControlStoreDep,
) -> KeyListResponse:
    """List all API keys (without secrets).

    Revoked keys are excluded by default.
    """
    keys = await store.list_keys(include_revoked=False)
    return KeyListResponse(
        keys=[
            KeyListItem(
                key_id=k.key_id,
                name=k.name,
                prefix=k.prefix,
                scopes=k.scopes,
                created_at=to_iso_format(k.created_at),
                updated_at=to_iso_format(k.updated_at),
                last_used_at=to_iso_format(k.last_used_at) if k.last_used_at else None,
                revoked_at=to_iso_format(k.revoked_at) if k.revoked_at else None,
            )
            for k in keys
        ]
    )


@router.delete(
    "/auth/keys/{key_id}",
    dependencies=[Depends(_require_admin)],
)
async def revoke_key(
    key_id: str,
    store: ControlStoreDep,
) -> dict[str, str]:
    """Revoke an API key (soft delete).

    The key remains in the database for audit purposes but can no longer
    be used for authentication.
    """
    revoked = await store.revoke_key(key_id)
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {"code": "not_found", "message": f"Key {key_id} not found"}
            },
        )
    return {"status": "revoked", "key_id": key_id}


@router.post(
    "/auth/keys/{key_id}/rotate",
    response_model=CreateKeyResponse,
    dependencies=[Depends(_require_admin)],
)
async def rotate_key(
    key_id: str,
    store: ControlStoreDep,
) -> CreateKeyResponse:
    """Rotate an API key.

    Generates a new secret for the key. The old secret is immediately
    invalidated. Returns the new secret (only returned once).
    """
    key = await store.rotate_key(key_id)
    if key is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {"code": "not_found", "message": f"Key {key_id} not found"}
            },
        )
    return CreateKeyResponse(
        key_id=key.key_id,
        name=key.name,
        key=key.secret,
        prefix=key.prefix,
        scopes=key.scopes,
        created_at=to_iso_format(key.created_at),
    )


DEFAULT_SESSION_SECRET = "everos-default-session-secret-change-me"


def _get_session_secret(request: Request) -> str:
    """Get the session signing secret from the Application settings."""
    app = request.app.state.application
    if app._settings.auth.session_secret:
        return app._settings.auth.session_secret.get_secret_value()
    return DEFAULT_SESSION_SECRET


def session_secret(request: Request) -> str:
    """Dependency for the session signing secret."""
    return _get_session_secret(request)


SessionSecretDep = Annotated[str, Depends(session_secret)]


def _sign_session(api_key_id: str, secret: str) -> str:
    """Sign a session payload."""
    payload = json.dumps({"key_id": api_key_id})
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_session(session_value: str, secret: str) -> str | None:
    """Verify a session cookie and return the key_id, or None if invalid."""
    if ":" not in session_value:
        return None
    payload, signature = session_value.rsplit(":", 1)
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(payload)
        return data.get("key_id")
    except json.JSONDecodeError:
        return None


@router.post("/auth/session", response_model=SessionResponse)
async def create_session(
    body: SessionRequest,
    response: Response,
    store: ControlStoreDep,
    secret: SessionSecretDep,
) -> SessionResponse:
    """Create a session by validating an API key.

    Sets an httpOnly, SameSite=Lax cookie that the console can use for
    subsequent requests.
    """
    api_key = await store.lookup_key(body.key)
    if api_key is None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthorized", "message": "Invalid API key"}},
        )

    session_value = _sign_session(api_key.key_id, secret)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_value,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return SessionResponse(ok=True, message="Session created")


@router.delete("/auth/session", response_model=SessionResponse)
async def delete_session(
    response: Response,
) -> SessionResponse:
    """Clear the session cookie (logout)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
    )
    return SessionResponse(ok=True, message="Session cleared")
