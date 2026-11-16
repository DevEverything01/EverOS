# Cloud-parity Progress

## Status

- [x] A1: Control-plane storage (`control.db`)
- [x] A2: Authentication middleware
- [x] A3: API-key management endpoints + session cookie
- [x] A4: Request logs middleware and admin endpoints
- [x] A5: Usage metering (quota TBD)
- [x] A6: Memory editing, feedback, context, tags
- [x] A7: Multi knowledge base with legacy alias
- [x] A8: Tasks shim (existing) + docs config
- [x] A9: Admin aggregates (overview, memories, agents)

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

### A4: Request logs

Request logging middleware and admin endpoints:
- Middleware records every `/api/` request (except exempt paths) to `request_logs`
- Increments usage counters (`add_calls`, `flush_calls`, `search_calls`, `get_calls`, `errors`)
- Prunes old logs on startup and hourly based on `retain_days`

Endpoints:
- `GET /api/v2/admin/request-logs` — List with filters (from, to, status, method, path, api_key_id)
- `GET /api/v2/admin/request-logs/{id}` — Single log entry

Files:
- `src/everos/entrypoints/api/middleware/request_logging.py` — RequestLoggingMiddleware
- `src/everos/entrypoints/api/routes/admin.py` — Admin route handlers

### A5: Usage metering

Usage statistics endpoint:
- `GET /api/v2/admin/usage?range=24h|7d|30d` — Bucketed usage data with totals
- Reads from `usage_daily` table (already incremented by request logging middleware)

### A8: Tasks shim + docs

- Tasks endpoint already exists (`GET /api/v2/tasks`, `GET /api/v2/tasks/{id}`)
- Added `ServerSettings` with `docs_enabled` config (default true)
- Updated `app.py` to use `settings.server.docs_enabled` instead of ENV check

Files:
- `src/everos/config/settings.py` — Added `ServerSettings` class

### A9: Admin aggregates

Dashboard endpoints:
- `GET /api/v2/admin/overview` — System overview (space, counts, model, version, auth mode)
- `GET /api/v2/admin/memories` — Browse memories without query vector (stub)
- `GET /api/v2/admin/memories/{id}` — Memory detail (stub)
- `GET /api/v2/admin/agents` — List agents (stub)

All require `admin` scope.

## Tests

- `tests/unit/test_infra/test_sqlite/test_control_store.py` — ControlStore tests
- `tests/unit/test_entrypoints/test_api/test_middleware/test_auth.py` — Auth middleware tests
- `tests/unit/test_entrypoints/test_api/test_middleware/test_request_logging.py` — Request logging tests
- `tests/unit/test_entrypoints/test_api/test_routes/test_admin.py` — Admin route tests

## Work log

### A1-A3 Complete
- All features implemented
- `make lint` passes
- Unit tests written and passing
- Running full `make test` to verify no regressions

### A4-A5, A8-A9 Complete
- Request logging middleware with usage metering
- Admin routes for request logs, usage, overview, memories, agents
- ServerSettings for docs_enabled config
- Tests added for middleware and routes

### A6: Memory editing, feedback, context, tags

Endpoints (Cloud-parity):
- `POST /api/v2/memory/edit` — Bulk-edit a user's profile items
- `POST /api/v2/memory/update` — Edit one episode's narrative/summary/subject
- `POST /api/v2/memory/feedback` — Rate a memory (stores in control.db)
- `POST /api/v2/memory/context` — Multi-round LLM-driven retrieval (stub: delegates to search)
- `POST /api/v2/memory/tags/add` — Add tags to a memory
- `POST /api/v2/memory/tags/remove` — Remove tags from a memory
- `GET /api/v2/memory/tags` — List tags for a memory or memories for a tag

Files:
- `src/everos/entrypoints/api/schemas/memory_edit.py` — Request/response DTOs
- `src/everos/entrypoints/api/routes/memory_edit.py` — Route handlers
- `src/everos/entrypoints/api/schemas/__init__.py` — Re-exports
- `src/everos/entrypoints/api/app.py` — Router registration

ControlStore additions:
- `add_feedback()` — Store feedback in control.db
- `add_tags()` — Add tags to memory
- `remove_tags()` — Remove tags (fixed SQL syntax for SQLite)
- `get_tags()` — List tags for memory
- `get_memories_by_tag()` — List memory ids by tag

### A7: Multi knowledge base

Endpoints (Cloud-parity):
- `POST /api/v2/knowledge_bases` — Create a knowledge base
- `GET /api/v2/knowledge_bases` — List knowledge bases
- `DELETE /api/v2/knowledge_bases/{kb_id}` — Delete a knowledge base
- `POST /api/v2/knowledge_bases/{kb_id}/documents` — Upload a document
- `GET /api/v2/knowledge_bases/{kb_id}/documents` — List documents
- `GET /api/v2/knowledge_bases/{kb_id}/documents/{doc_id}` — Get document detail
- `GET /api/v2/knowledge_bases/{kb_id}/categories` — List categories
- `GET /api/v2/knowledge_bases/{kb_id}/topics/{topic_id}` — Get topic detail
- `POST /api/v2/knowledge_bases/{kb_id}/search` — Search knowledge

Legacy alias: `/api/v2/knowledge/*` continues to work via existing router.

Files:
- `src/everos/entrypoints/api/schemas/knowledge_bases.py` — Request/response DTOs
- `src/everos/entrypoints/api/routes/knowledge_bases.py` — Route handlers
- `src/everos/entrypoints/api/schemas/__init__.py` — Re-exports
- `src/everos/entrypoints/api/app.py` — Router registration

Tests:
- `tests/unit/test_entrypoints/test_api/test_routes/test_memory_edit.py` — 11 tests
- `tests/unit/test_entrypoints/test_api/test_routes/test_knowledge_bases.py` — 10 tests
