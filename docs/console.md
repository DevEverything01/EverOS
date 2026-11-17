# Web Console

EverOS ships with a bundled web console for managing memory, viewing
request logs, and administering API keys without external tools.

## Table of contents

- [Overview](#overview)
- [Enabling the console](#enabling-the-console)
- [Accessing the console](#accessing-the-console)
- [Authentication](#authentication)
- [Pages](#pages)
  - [Dashboard](#dashboard)
  - [Memory Bank](#memory-bank)
  - [Request Logs](#request-logs)
  - [API Keys](#api-keys)
  - [Knowledge](#knowledge)
  - [Playground](#playground)
  - [Settings](#settings)
- [Technical details](#technical-details)

## Overview

The console is a single-page application (SPA) built with vanilla HTML,
CSS, and JavaScript. It has no build step, no Node.js toolchain, and
no external CDN dependencies. All assets, including the Manrope font,
are bundled in the Python wheel.

## Enabling the console

The console is **enabled by default**. Control it with:

```bash
# Disable the console
everos server start --no-console

# Enable the console (default)
everos server start --console
```

Or via configuration:

```toml
[server]
console_enabled = true
```

Environment variable:

```bash
EVEROS_SERVER__CONSOLE_ENABLED=true
```

## Accessing the console

Once the server is running, open your browser to:

```
http://127.0.0.1:8000/console
```

The console is served at `/console` and all its assets under
`/console/*`.

## Authentication

If authentication is enabled (the default), the console displays a
login page. Enter a valid API key with at least `memory:read` scope
to access read-only features, or an `admin`-scoped key for full access.

The console stores a session cookie (`everos_session`) that is:

- `httpOnly` (not accessible to JavaScript)
- `SameSite=Lax` (protects against CSRF)
- Valid for 7 days

Click "Sign Out" to clear the session.

## Pages

### Dashboard

The landing page shows:

- **Usage chart** with call counts over time
- **Stat tiles** (profiles, episodes, documents, etc.)
- **Recent requests** list
- **Instance info** (version, auth mode, storage path)

### Memory Bank

Browse and manage memories across three tabs:

| Tab | Content |
|---|---|
| **Overview** | Summary counts by memory type |
| **User** | User profiles and episodes |
| **Agent** | Agent cases and skills |

Write actions available:

- **Edit profile items** (add, update, delete)
- **Edit episodes** (update summary, subject, narrative)
- **Give feedback** (thumbs up/down with optional note)
- **Manage tags** (add/remove tags on memories)

### Request Logs

View API request history with filters:

| Filter | Description |
|---|---|
| **Method** | GET, POST, DELETE, etc. |
| **Status** | HTTP status code (200, 401, 500, etc.) |
| **Path** | URL path prefix |
| **Time range** | From/to timestamps |

Each row shows:

- Timestamp
- Method and path
- Status code
- Latency (ms)
- API key ID (if authenticated)

Logs are retained for 14 days by default. Configure retention with:

```toml
[server.request_logs]
retain_days = 14
```

### API Keys

Manage API keys for the instance:

| Action | Description |
|---|---|
| **Create** | Generate a new key with selected scopes |
| **Rotate** | Generate a new secret for an existing key |
| **Revoke** | Soft-delete a key (keeps audit trail) |

The key secret is shown **once** after creation or rotation. Copy it
immediately; it cannot be retrieved later.

### Knowledge

Manage knowledge bases and documents:

- **Create knowledge base** with name and description
- **Upload documents** (text or file upload)
- **Browse documents** by category
- **Delete knowledge base** (with confirmation)

### Playground

Test memory operations interactively:

| Mode | Description |
|---|---|
| **Search** | Query memories with filters and methods |
| **Context** | Multi-round LLM-driven retrieval |
| **Add** | Add new memory messages |

### Settings

View instance configuration:

- Storage paths (memory root, index directory)
- Authentication mode
- Server version
- Links to API documentation

## Technical details

The console is served as static files from the `everos.web` package:

```
src/everos/web/
  __init__.py
  static/
    index.html
    app.js
    app.css
    font.css
    manrope-latin.woff2
    manrope-latin-ext.woff2
```

The SPA uses hash-based routing (`#/dashboard`, `#/memory`, etc.).
Any unknown path under `/console/` falls back to `index.html`.

Path traversal is blocked; requests outside the static directory
return 404.

Caching headers:

| Asset type | Cache-Control |
|---|---|
| Fonts (`.woff2`) | `max-age=86400` (1 day) |
| Other assets | `no-store` |

---

For authentication details, see [auth.md](auth.md). For API endpoint
reference, see [api.md](api.md).
