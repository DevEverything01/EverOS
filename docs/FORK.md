# Fork Maintenance

This document describes how to maintain a fork of EverOS that adds
features not present in upstream, specifically the cloud-parity
additions in the `feat/cloud-parity` branch.

## Overview

The `feat/cloud-parity` branch extends upstream EverOS with:

- **Authentication** (API keys, session cookies)
- **Admin endpoints** (request logs, usage, overview)
- **Memory editing** (profile edit, episode update, feedback, tags)
- **Multi knowledge base** support
- **Bundled web console**

These features are additive; existing API contracts and storage
formats are unchanged.

## Upstream sync workflow

When a new upstream release is available:

1. **Fetch upstream changes:**

   ```bash
   git remote add upstream https://github.com/EverMind-AI/EverOS.git
   git fetch upstream
   ```

2. **Review the changelog:**

   Check upstream `CHANGELOG.md` for breaking changes or conflicts
   with fork additions.

3. **Merge or rebase:**

   ```bash
   # Option A: Merge (preserves history)
   git checkout feat/cloud-parity
   git merge upstream/main

   # Option B: Rebase (cleaner history, more conflict potential)
   git checkout feat/cloud-parity
   git rebase upstream/main
   ```

4. **Resolve conflicts:**

   Common conflict areas:
   - `src/everos/entrypoints/api/app.py` (router registration)
   - `src/everos/config/settings.py` (new settings classes)
   - `docs/api.md` (endpoint documentation)

5. **Run the full test suite:**

   ```bash
   make lint
   make test
   make integration
   ```

6. **Update documentation:**

   If upstream added endpoints, update `docs/api.md` to include both
   the upstream additions and any authentication requirements.

## Protected files

Do not overwrite these files during sync:

| Path | Reason |
|---|---|
| `CLAUDE.md` | Fork-specific instructions |
| `.claude/skills/*` | Fork workflows |
| `CONTRIBUTING.md` | Fork contribution guide |
| `.github/*` | Fork CI/CD workflows |
| `docs/FORK.md` | This document |
| `docs/auth.md` | Fork authentication docs |
| `docs/console.md` | Fork console docs |

See [github-sync.md](github-sync.md) for the full protected list.

## Fork-specific features

### Authentication middleware

Files:
- `src/everos/entrypoints/api/middleware/auth.py`
- `src/everos/entrypoints/api/middleware/request_logging.py`

The middleware checks for `Authorization: Bearer <key>` or
`X-Api-Key: <key>` headers. It attaches `api_key_id` and `scopes`
to `request.state` for downstream use.

### Control-plane storage

Files:
- `src/everos/infra/persistence/sqlite/control_store.py`
- `src/everos/infra/persistence/sqlite/_tables/control.py`

Separate `control.db` database for:
- API keys
- Request logs
- Usage metrics
- Feedback
- Tags

### Admin routes

Files:
- `src/everos/entrypoints/api/routes/auth.py`
- `src/everos/entrypoints/api/routes/admin.py`

All require `admin` scope.

### Memory editing routes

Files:
- `src/everos/entrypoints/api/routes/memory_edit.py`
- `src/everos/entrypoints/api/schemas/memory_edit.py`

Cloud-parity endpoints for editing profiles and episodes.

### Web console

Files:
- `src/everos/web/static/*`
- `src/everos/entrypoints/api/routes/console.py`

Vanilla JS SPA served at `/console`.

## Testing fork features

Unit tests:
```bash
pytest tests/unit/test_entrypoints/test_api/test_middleware/
pytest tests/unit/test_entrypoints/test_api/test_routes/test_auth.py
pytest tests/unit/test_entrypoints/test_api/test_routes/test_admin.py
pytest tests/unit/test_entrypoints/test_api/test_routes/test_console.py
pytest tests/unit/test_entrypoints/test_api/test_routes/test_memory_edit.py
pytest tests/unit/test_entrypoints/test_api/test_routes/test_knowledge_bases.py
pytest tests/unit/test_infra/test_sqlite/test_control_store.py
```

Integration smoke test:
```bash
./scripts/smoke_console.sh
```

## Versioning

Fork releases follow upstream versioning with a suffix:

- Upstream: `1.7.0`
- Fork: `1.7.0+cloud.1`, `1.7.0+cloud.2`, etc.

The suffix is not reflected in `__version__` to avoid compatibility
issues; it appears only in release tags and the changelog.

---

For upstream documentation, see the EverOS repository at
https://github.com/EverMind-AI/EverOS.
