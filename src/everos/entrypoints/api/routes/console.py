"""Console static file serving.

Serves the bundled web console at ``/console``. The console is a vanilla
HTML/CSS/JS SPA with no build step. Static assets are served from the
``web/static`` directory shipped as package data.

The router is only mounted when ``settings.server.console_enabled`` is true.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["console"])

MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

_STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "static"

CONSOLE_CSP = (
    "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; "
    "img-src 'self' data:; frame-ancestors 'none'"
)
"""Content-Security-Policy for every console response.

The console ships no inline script, handler or style attribute (markup carries
``data-act`` / ``data-style`` and ``app.js`` wires them), so ``'self'`` is
enough. ``img-src data:`` covers the favicon and the CSS select arrow."""

_SECURITY_HEADERS = {
    "Content-Security-Policy": CONSOLE_CSP,
    "X-Content-Type-Options": "nosniff",
}


def _serve_index() -> FileResponse:
    """Serve the main index.html file."""
    index_path = _STATIC_DIR / "index.html"
    return FileResponse(
        index_path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store", **_SECURITY_HEADERS},
    )


@router.get("/console", response_model=None, include_in_schema=False)
async def console_root() -> FileResponse:
    """Serve the console index page."""
    return _serve_index()


@router.get("/console/", response_model=None, include_in_schema=False)
async def console_root_slash() -> FileResponse:
    """Serve the console index page (trailing slash)."""
    return _serve_index()


@router.get("/console/{path:path}", response_model=None, include_in_schema=False)
async def console_assets(path: str) -> FileResponse | HTMLResponse:
    """Serve console static assets or SPA fallback.

    Static files (css, js, fonts, images) are served directly with appropriate
    caching headers. Unknown paths fall back to index.html for SPA routing.
    """
    target = (_STATIC_DIR / path).resolve()

    if not str(target).startswith(str(_STATIC_DIR)):
        return _serve_index()

    if target.is_file():
        suffix = target.suffix.lower()
        media_type = MIME_TYPES.get(suffix, "application/octet-stream")
        cache = (
            "public, max-age=86400"
            if suffix in {".woff2", ".png", ".svg", ".ico"}
            else "no-store"
        )
        headers = {"Cache-Control": cache, **_SECURITY_HEADERS}
        return FileResponse(target, media_type=media_type, headers=headers)

    return _serve_index()
