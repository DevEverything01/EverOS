# Cloud-parity Progress

## Status

### Part A: Backend parity
- [x] A1: Control-plane storage (`control.db`)
- [x] A2: Authentication middleware
- [x] A3: API-key management endpoints + session cookie
- [x] A4: Request logs middleware and admin endpoints
- [x] A5: Usage metering (quota TBD)
- [x] A6: Memory editing, feedback, context, tags
- [x] A7: Multi knowledge base with legacy alias
- [x] A8: Tasks shim (existing) + docs config
- [x] A9: Admin aggregates (overview, memories, agents)

### Part B: Bundled console
- [x] B1: Serve console from app at `/console`
- [x] B2: Package data (web assets shipped in wheel)
- [x] B3: CLI `--console/--no-console` flag
- [x] B4: Login page with session cookie auth
- [x] B5: English-first UI (language policy compliant)
- [x] B6: Write actions (edit profile, episodes, feedback, tags, keys, KB)
- [x] B7: All pages (Dashboard, Memory Bank, Logs, Keys, Knowledge, Playground, Settings)

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

## Part B: Bundled Console

- [x] B1: Serve console from app at `/console`
- [x] B2: Package data (web assets shipped in wheel)
- [x] B3: CLI `--console/--no-console` flag
- [x] B4: Login page with session cookie auth
- [x] B5: English-first UI (language policy compliant)
- [x] B6: Write actions (edit profile, episodes, feedback, tags, keys, KB)
- [x] B7: All pages (Dashboard, Memory Bank, Request Logs, API Keys, Knowledge, Playground, Settings)

### B1: Console routing

Static file serving at `/console` with SPA fallback:
- `GET /console` and `GET /console/` serve `index.html`
- `GET /console/{path}` serves static assets or falls back to index.html
- Path traversal attempts blocked (resolve + prefix check)
- Appropriate caching headers (fonts: 86400s, others: no-store)

Files:
- `src/everos/entrypoints/api/routes/console.py` — Console router
- `src/everos/entrypoints/api/app.py` — Conditional mount when `console_enabled`

### B2: Package data

Web assets moved into the Python package:
- `src/everos/web/__init__.py` — Package marker with docstring
- `src/everos/web/static/` — HTML, CSS, JS, fonts (bundled Manrope woff2)

The wheel includes these files via hatch's standard package handling.

### B3: CLI flag

Added `--console/--no-console` to `everos server start`:
- Sets `EVEROS_SERVER__CONSOLE_ENABLED` environment variable
- Default: enabled (console_enabled=True in ServerSettings)

Files:
- `src/everos/entrypoints/cli/commands/server.py` — Flag handling
- `src/everos/config/settings.py` — `ServerSettings.console_enabled`

### B4-B7: Console UI

Full-featured vanilla JS console with:
- **Auth**: Login page posts to `/api/v2/auth/session`, uses cookies
- **Dashboard**: Usage chart, stat tiles, recent requests, instance info
- **Memory Bank**: Overview/User/Agent tabs, profile editing, episode editing, feedback (+/-)
- **Request Logs**: Filters (method, status, path), latency column, paginated
- **API Keys**: Create/rotate/revoke with confirm dialogs
- **Knowledge**: Create KB, upload docs, delete KB
- **Playground**: Search + Context + Add modes
- **Settings**: Instance info, links to API docs

Visual fidelity: 240px sidebar, amber accent (#f5c542), white cards, Manrope font.
Safety: Confirm dialogs for destructive actions, secrets masked.

Files:
- `src/everos/web/static/index.html` — SPA shell with modal and login overlays
- `src/everos/web/static/app.js` — Full console application
- `src/everos/web/static/app.css` — Styles including modal/login
- `src/everos/web/static/font.css` — Manrope font-face
- `src/everos/web/static/manrope-*.woff2` — Bundled fonts

### Tests

- `tests/unit/test_entrypoints/test_api/test_routes/test_console.py` — 7 tests
  - Root serves HTML
  - Slash serves HTML
  - CSS/JS/fonts served correctly
  - SPA fallback for unknown paths
  - Path traversal blocked

## Run 6: Real persistence and console alignment

### Real persistence (no stubs)

The following endpoints now persist changes to actual storage:

- `POST /api/v2/memory/edit` — Profile editing persists to `user.md` through the
  same `MarkdownProfileStore` the extractor uses. Add/update/delete operations
  modify `explicit_info` or `implicit_traits` lists. New items get stable
  `item_id`s (format `ei_<8hex>` / `it_<8hex>`).

- `POST /api/v2/memory/update` — Episode update rewrites entries in daily
  `episode-YYYY-MM-DD.md` files. Removes the old entry, appends the modified one
  to maintain entry marker validity. The cascade enqueues index sync.

- `POST /api/v2/memory/feedback` — Already persisted to `control.db` feedback
  table. Added `GET /api/v2/memory/feedback?memory_id=` read path.

- `POST /api/v2/memory/context` — Runs hybrid search and assembles a prompt-ready
  context string from episode summaries and profile facts.

Files:
- `src/everos/service/memory/_edit.py` — Edit operations (profile, episode)
- `src/everos/service/memory/__init__.py` — Re-exports and MemoryService methods
- `src/everos/entrypoints/api/routes/memory_edit.py` — Updated route handlers
- `src/everos/entrypoints/api/schemas/memory_edit.py` — FeedbackItem, FeedbackListResponse
- `src/everos/infra/persistence/sqlite/control_store.py` — get_feedback method

### Console alignment

Fixed `app.js` to match the implemented API contracts:

- Profile editing sends `operations: [{action, type, item_id?, data: {...}}]`
- Episode update sends `{memory_type, memory_id, patch: {...}}`
- Feedback sends `{memory_type, memory_id, rating: "positive"|"negative"}`
- User tab reads profile shape: `profile_data.explicit_info` / `implicit_traits`
- Fixed settings page bug referencing undefined `c` variable

### Tests

- `tests/unit/test_service/test_memory/test_edit.py` — 8 tests for edit operations
- `tests/fakes/application.py` — Added edit_profile, update_episode to StubMemoryService
- `tests/unit/test_memory/test_event_ts_pinned.py` — Updated allowed ts count
- `scripts/smoke_console.sh` — Extended with profile persistence verification,
  context endpoint test, feedback persistence and retrieval tests
