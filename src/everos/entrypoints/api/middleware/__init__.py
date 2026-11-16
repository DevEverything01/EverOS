"""The HTTP API layer's middleware (CORS, profiling, metrics, request id, auth).

External usage::

    from everos.entrypoints.api.middleware import (
        DEFAULT_CORS_ALLOW_CREDENTIALS,
        DEFAULT_CORS_ALLOW_HEADERS,
        DEFAULT_CORS_ALLOW_METHODS,
        DEFAULT_CORS_ORIGINS,
        AuthMiddleware,
        ProfileMiddleware,
        PrometheusMiddleware,
        require_scope,
    )
"""

from .auth import AuthMiddleware as AuthMiddleware
from .auth import require_any_scope as require_any_scope
from .auth import require_scope as require_scope
from .cors import DEFAULT_CORS_ALLOW_CREDENTIALS as DEFAULT_CORS_ALLOW_CREDENTIALS
from .cors import DEFAULT_CORS_ALLOW_HEADERS as DEFAULT_CORS_ALLOW_HEADERS
from .cors import DEFAULT_CORS_ALLOW_METHODS as DEFAULT_CORS_ALLOW_METHODS
from .cors import DEFAULT_CORS_ORIGINS as DEFAULT_CORS_ORIGINS
from .profile import ProfileMiddleware as ProfileMiddleware
from .prometheus import PrometheusMiddleware as PrometheusMiddleware
from .request_id import RequestIdMiddleware as RequestIdMiddleware
from .write_tracking import WriteTrackingMiddleware as WriteTrackingMiddleware

__all__ = [
    "DEFAULT_CORS_ALLOW_CREDENTIALS",
    "DEFAULT_CORS_ALLOW_HEADERS",
    "DEFAULT_CORS_ALLOW_METHODS",
    "DEFAULT_CORS_ORIGINS",
    "AuthMiddleware",
    "ProfileMiddleware",
    "PrometheusMiddleware",
    "RequestIdMiddleware",
    "WriteTrackingMiddleware",
    "require_any_scope",
    "require_scope",
]
