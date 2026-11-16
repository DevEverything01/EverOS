# Cloud-parity Progress

## Status

- [x] A1: Control-plane storage (`control.db`)
- [x] A2: Authentication middleware
- [x] A3: API-key management endpoints + session cookie

## Implementation Summary

### A1: Control-plane storage

Created `control.db` with tables:
- `api_keys`: Key registry (sha256 hash stored, secret never persisted)
- `request_logs`: Per-request audit log
- `usage_daily`: Daily usage counters by metric
- `feedback`: User feedback on memories
- `tags`: User-assigned tags on memories
- `schema_version`: Migration tracking (one row)

Files:
- `src/everos/infra/persistence/sqlite/_tables/control.py` — SQLModel tables
- `src/everos/infra/persistence/sqlite/control_store.py` — ControlStore manager
- `src/everos/infra/persistence/sqlite/__init__.py` — Re-exports ControlStore
- `src/everos/infra/persistence/memory_root.py` — Added `control_db` property

### A2: Authentication middleware

Enforces Bearer/X-Api-Key authentication:
- Extracts token from `Authorization: Bearer <key>` or `X-Api-Key: <key>`
- Looks up key by sha256 hash in control store
- Rejects revoked/unknown keys with HTTP 401
- Attaches `api_key_id` and `scopes` to `request.state` on success
- Skips exempt paths (`/health`, `/metrics`, `/docs`, `/console`, etc.)
- Supports bootstrap token for first-run key creation

Files:
- `src/everos/entrypoints/api/middleware/auth.py` — AuthMiddleware class
- `src/everos/entrypoints/api/middleware/__init__.py` — Re-exports
- `src/everos/config/settings.py` — AuthSettings, RequestLogsSettings

### A3: API-key management endpoints

Endpoints:
- `POST /api/v2/auth/keys` — Create new API key (returns secret once)
- `GET /api/v2/auth/keys` — List all keys (without secrets)
- `DELETE /api/v2/auth/keys/{key_id}` — Revoke key (soft delete)
- `POST /api/v2/auth/keys/{key_id}/rotate` — Rotate key (new secret)
- `POST /api/v2/auth/session` — Set session cookie for console
- `DELETE /api/v2/auth/session` — Clear session cookie

Files:
- `src/everos/entrypoints/api/routes/auth.py` — Route handlers
- `src/everos/entrypoints/api/dependencies.py` — ControlStoreDep, RequireAdminDep

## Decisions

1. **Control-plane location**: `ControlStore` in `infra/persistence/sqlite/`
   following the existing adapter pattern. Control data is not memory domain.

2. **Separate database**: `control.db` sits beside `system.db` under
   `.index/sqlite/`. Same engine pattern, independent schema and lifecycle.

3. **Schema versioning**: Single `schema_version` table with one row; each
   migration checks current version and upgrades idempotently.

4. **Key format**: `eos_<8-char prefix>_<32-byte urlsafe>` per the brief.
   Store sha256 hash of the full key; only prefix is retrievable.

5. **Scopes**: `memory:read`, `memory:write`, `admin`. Admin grants all.

6. **Test compatibility**: Added `EVEROS_AUTH__LEGACY_OPEN=true` fixture to
   conftest.py so existing tests pass without API keys.

## Tests

- `tests/unit/test_infra/test_sqlite/test_control_store.py` — ControlStore tests
- `tests/unit/test_entrypoints/test_api/test_middleware/test_auth.py` — Middleware tests

## Work log

### A1-A3 Complete
- All features implemented
- `make lint` passes
- Unit tests written and passing
- Running full `make test` to verify no regressions
