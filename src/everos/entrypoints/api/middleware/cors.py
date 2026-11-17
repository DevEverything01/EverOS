"""CORS configuration defaults.

The CORS middleware itself is FastAPI's stock ``CORSMiddleware``; this module
centralises the default policy values used by the application factory.
"""

from __future__ import annotations

DEFAULT_CORS_ALLOW_CREDENTIALS: bool = False
DEFAULT_CORS_ALLOW_HEADERS: list[str] = ["*"]
DEFAULT_CORS_ALLOW_METHODS: list[str] = ["*"]
DEFAULT_CORS_ORIGINS: list[str] = ["*"]


def effective_allow_credentials(origins: list[str], allow_credentials: bool) -> bool:
    """Credentials are never allowed together with the ``"*"`` origin.

    Starlette answers a wildcard-origin, credentialed request by echoing the
    caller's ``Origin`` -- any site could then read responses with the
    console's session cookie. Credentials need an explicit origin allowlist.
    """
    return allow_credentials and "*" not in origins
