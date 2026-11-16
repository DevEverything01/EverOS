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
| `memory:read` | Read access to memory endpoints (`/search`, `/get`, `/context`) |
| `memory:write` | Write access (`/add`, `/flush`, `/edit`, `/update`, `/delete`, `/feedback`, `/tags`) |
| `admin` | Full access including key management, request logs, usage, and overview |

The `admin` scope implicitly grants all other scopes.

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

The bootstrap token is accepted until the first API key is created.
After that, it is no longer valid and must be removed from your
configuration.

**Security note:** The bootstrap token should be a long, random string.
Remove it from your environment after creating your first admin key.

## Session-based authentication

The bundled console uses session cookies for authentication:

1. User enters an API key on the login page
2. Console posts to `POST /api/v2/auth/session` with the key
3. Server validates the key and sets an `httpOnly` cookie
4. Subsequent requests use the cookie automatically

Sessions expire after 7 days. To log out, call `DELETE /api/v2/auth/session`.

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
# Leave unset to use a default; set a secure random value in production
# session_secret = ""

# Paths that do not require authentication
exempt_paths = ["/health", "/metrics", "/docs", "/openapi.json", "/redoc", "/console"]
```

---

For API endpoint details, see [api.md](api.md). For console usage, see
[console.md](console.md).
