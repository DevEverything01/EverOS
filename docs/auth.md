# Authentication

EverOS supports API-key authentication for securing access to the HTTP API.
When enabled, all API requests (except exempt paths) must include a valid
API key.

## Table of contents

- [Overview](#overview)
- [Enabling authentication](#enabling-authentication)
- [API keys](#api-keys)
  - [Key format](#key-format)
  - [Scopes](#scopes)
  - [Creating keys](#creating-keys)
  - [Rotating keys](#rotating-keys)
  - [Revoking keys](#revoking-keys)
- [Bootstrap token](#bootstrap-token)
- [Session-based authentication](#session-based-authentication)
- [Migration from open access](#migration-from-open-access)
- [Exempt paths](#exempt-paths)
- [Configuration reference](#configuration-reference)

## Overview

Authentication is **enabled by default** for new installations. Existing
installations that upgrade from a version without authentication will
continue to work in open mode until explicitly configured.

The authentication system uses:

- **API keys** for programmatic access (SDKs, CLI, MCP)
- **Session cookies** for the bundled web console

## Enabling authentication

Authentication is controlled by the `[auth]` section in your configuration:

```toml
[auth]
enabled = true
legacy_open = false
```

| Setting | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable authentication middleware |
| `legacy_open` | `false` | When `true`, bypass authentication (for existing deployments) |

Environment variables:

```bash
EVEROS_AUTH__ENABLED=true
EVEROS_AUTH__LEGACY_OPEN=false
```

## API keys

### Key format

API keys follow the format:

```
eos_<8-char prefix>_<32-byte urlsafe secret>
```

Example: `eos_a1b2c3d4_xN7kP9mQ2vL8wR4yH6jT3sF5gV1nB0cK...`

The prefix is stored and can be used to identify keys without revealing
the secret. Only the SHA-256 hash of the full key is stored in the
database.

### Scopes

Each key has one or more scopes that control access:

| Scope | Grants |
|---|---|
| `memory:read` | Read routes: `POST /memory/search`, `/memory/get`, `/memory/context`; `GET /memory/feedback`, `/memory/tags`, `/memory/purge/{job_id}`; `GET /tasks*`; knowledge reads (`GET /knowledge/documents*`, `/knowledge/topics/*`, `/knowledge/categories`, `POST /knowledge/search`, and the `GET` / `POST .../search` routes under `/knowledge_bases`) |
| `memory:write` | Write routes: `/memory/add`, `/flush`, `/edit`, `/update`, `/delete`, `/delete_by_ids`, `/feedback`, `/tags/add`, `/tags/remove`, `/purge*`; knowledge document and knowledge-base writes; `POST /ome/trigger` |
| `admin` | Full access including key management, request logs, usage, overview, and `POST /cascade/quiesce` |

The `admin` scope implicitly grants all other scopes; `memory:write` does
**not** imply `memory:read` -- a write-only key (an ingestion pipeline, say)
cannot read memory back. Key management (`/api/v2/auth/keys*`), every
`/api/v2/admin/*` route and `POST /cascade/quiesce` (which stops the index
worker for the life of the process) require `admin`. A key without the
required scope gets `403 FORBIDDEN`.

When the console authenticates with its session cookie, every unsafe request
(`POST` / `PUT` / `PATCH` / `DELETE`) must also carry an `Origin` (or, failing
that, `Referer`) of the same origin as the request; a cross-site one is
refused with `403 CROSS_SITE_REFUSED`. Requests authenticated with
`X-Api-Key` or `Authorization: Bearer` are not subject to this check, since a
browser never attaches those headers on its own.

### Creating keys

Use the `POST /api/v2/auth/keys` endpoint:

```bash
curl -X POST http://127.0.0.1:8000/api/v2/auth/keys \
  -H "Authorization: Bearer <admin_key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-service", "scopes": ["memory:read", "memory:write"]}'
```

Response:

```json
{
  "key_id": "key_a1b2c3d4",
  "name": "my-service",
  "key": "eos_a1b2c3d4_xN7kP9mQ2vL8wR4yH6jT3sF5gV1nB0cK...",
  "prefix": "a1b2c3d4",
  "scopes": ["memory:read", "memory:write"],
  "created_at": "2026-11-16T10:00:00Z"
}
```

**Important:** The `key` field contains the full secret and is only
returned once. Store it securely; it cannot be retrieved later.

### Rotating keys

Rotate a key to generate a new secret while keeping the same `key_id`:

```bash
curl -X POST http://127.0.0.1:8000/api/v2/auth/keys/{key_id}/rotate \
  -H "Authorization: Bearer <admin_key>"
```

The old secret is immediately invalidated.

### Revoking keys

Revoke a key (soft delete) to prevent further use:

```bash
curl -X DELETE http://127.0.0.1:8000/api/v2/auth/keys/{key_id} \
  -H "Authorization: Bearer <admin_key>"
```

Revoked keys remain in the database for audit purposes but cannot be
used for authentication.

## Bootstrap token

For first-time setup when no keys exist, you can use a bootstrap token:

```bash
export EVEROS_AUTH__BOOTSTRAP_TOKEN="your-secure-bootstrap-token"
everos server start
```

The bootstrap token (compared in constant time) is accepted only while no
API key exists. The first request that uses it creates one `bootstrap-admin`
key -- exactly once, even with concurrent requests or several processes --
and writes that key's secret to `<memory root>/bootstrap-admin.key` with
mode `0600` (the path is logged as `bootstrap_key_created`). Use that key
from then on: the bootstrap token stops working as soon as the key exists.
Move the secret somewhere safe and delete the file.

**Security note:** The bootstrap token should be a long, random string.
Remove it from your environment after creating your first admin key.

## Session-based authentication

The bundled console uses session cookies for authentication:

1. User enters an API key on the login page
2. Console posts to `POST /api/v2/auth/session` with the key
3. Server validates the key and sets an `httpOnly` cookie
4. Subsequent requests use the cookie automatically

Sessions expire after 7 days; the expiry is signed into the cookie and
enforced by the server, not only by the browser. A session is bound to the
key's current secret, so rotating or revoking the key ends every session
opened with it. The cookie is `Secure` when the login request arrived over
HTTPS (or, with `trust_forwarded_proto = true`, with
`X-Forwarded-Proto: https` from your TLS-terminating proxy). To log out, call
`DELETE /api/v2/auth/session`.

The signing secret is `session_secret` when set; otherwise a random secret
is generated on first use and stored in `control.db` (table
`control_settings`, key `auth.session_secret`), so sessions survive
restarts.

## Migration from open access

If you are upgrading from an open (unauthenticated) installation:

1. **Keep open mode temporarily:**

   ```toml
   [auth]
   enabled = true
   legacy_open = true
   ```

2. **Create your first admin key:**

   ```bash
   curl -X POST http://127.0.0.1:8000/api/v2/auth/keys \
     -H "Content-Type: application/json" \
     -d '{"name": "admin", "scopes": ["admin"]}'
   ```

3. **Store the returned key securely.**

4. **Disable legacy open mode:**

   ```toml
   [auth]
   enabled = true
   legacy_open = false
   ```

5. **Update your clients** to include the API key in requests.

## Exempt paths

The following paths do not require authentication:

| Path | Purpose |
|---|---|
| `/health` | Health check probe |
| `/metrics` | Prometheus metrics |
| `/docs` | Swagger UI |
| `/redoc` | ReDoc UI |
| `/openapi.json` | OpenAPI spec |
| `/console` | Web console (uses session auth) |
| `/console/*` | Console static assets |

Configure additional exempt paths:

```toml
[auth]
exempt_paths = ["/health", "/metrics", "/docs", "/openapi.json", "/redoc", "/console"]
```

## Configuration reference

Full `[auth]` section with defaults:

```toml
[auth]
# Enable authentication middleware
enabled = true

# Bypass authentication (for migration from open access)
legacy_open = false

# Bootstrap token for first-run key creation (env: EVEROS_AUTH__BOOTSTRAP_TOKEN)
# bootstrap_token = ""

# Session signing secret (env: EVEROS_AUTH__SESSION_SECRET)
# Leave unset to generate a random one, persisted in control.db
# session_secret = ""

# Trust X-Forwarded-Proto for the session cookie's Secure flag
# (enable only behind a TLS-terminating reverse proxy)
trust_forwarded_proto = false

# Paths that do not require authentication
exempt_paths = ["/health", "/metrics", "/docs", "/openapi.json", "/redoc", "/console"]
```

---

For API endpoint details, see [api.md](api.md). For console usage, see
[console.md](console.md).

## Proxies, CORS and request logs

- `[request_logs] trust_forwarded_for` (default `false`): record the client
  from the left-most `X-Forwarded-For` hop (validated as an IP address).
  Leave it off unless a reverse proxy sets the header; otherwise the socket
  peer is recorded and a client-supplied header is ignored.
- CORS defaults to `allow_origins = ["*"]` **without** credentials. Allowing
  credentials requires an explicit origin list; with `"*"` the server turns
  credentials off and logs `cors_credentials_disabled`.
- `/docs`, `/redoc` and `/openapi.json` are served only with `ENV=DEV` or an
  explicit `[server] docs_enabled = true`.
