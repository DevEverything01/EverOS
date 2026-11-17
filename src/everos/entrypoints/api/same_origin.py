"""Same-origin check for requests a browser may send on a user's behalf.

A browser attaches cookies to a cross-site "simple" request (a form or
``multipart/form-data`` POST needs no CORS preflight), so a page of another
site -- a sibling subdomain, another port on the same host -- can make a
logged-in console write. Browsers always send ``Origin`` on such requests
(``Referer`` as a fallback), so comparing it with the request's ``Host``
tells the console's own pages apart from everything else.

Requests with neither header are not browser cross-site requests (curl,
SDKs, plugins) and pass. Shared by the auth middleware (every unsafe request
authenticated by the session cookie) and the purge routes (every purge,
whatever authenticated it).
"""

from __future__ import annotations

__all__ = ["UNSAFE_METHODS", "is_cross_site"]

from typing import Final
from urllib.parse import urlsplit

from starlette.requests import Request

UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_cross_site(request: Request) -> bool:
    """Whether ``Origin`` (else ``Referer``) names another host than ``Host``.

    ``Origin: null`` (sandboxed frames, some redirects) counts as cross-site.
    """
    source = request.headers.get("origin") or request.headers.get("referer")
    if source is None:
        return False
    host = request.headers.get("host", "").lower()
    return urlsplit(source).netloc.lower() != host
