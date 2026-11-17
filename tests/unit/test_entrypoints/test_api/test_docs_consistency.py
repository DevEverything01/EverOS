"""What the docs, the console and the route docstrings say matches the code.

Before: ``docs/api.md`` still described ``/admin/usage?range=7d`` as counting
both end days (8 buckets); the console linked "OpenAPI (Swagger)" even when
``docs_enabled=false`` made ``/docs`` a 404; and ``/memory/update`` claimed to
"enqueue index sync", which it never did.
"""

from __future__ import annotations

import re
from pathlib import Path

from everos.entrypoints.api.routes.memory_edit import update_memory

REPO = Path(__file__).resolve().parents[4]
STATIC = REPO / "src" / "everos" / "web" / "static"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n#### ", start + len(heading))
    return text[start : nxt if nxt != -1 else None]


def test_api_md_states_usage_range_is_the_last_n_days() -> None:
    usage = _section(
        (REPO / "docs" / "api.md").read_text(), "#### GET /api/v2/admin/usage"
    )

    assert "including today" in usage
    assert "8 days" in usage  # the old behaviour is named, so upgraders notice


def test_changelog_records_the_usage_range_change() -> None:
    unreleased = (REPO / "CHANGELOG.md").read_text().split("\n## [1.")[0]

    assert re.search(r"admin/usage\?range=7d\|30d.*last 7 / 30", unreleased, re.S)


def test_console_api_docs_links_follow_docs_enabled() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()

    nav_link = re.search(r'<a href="/docs"[^>]*>', html)
    assert nav_link is not None
    assert 'id="apiDocsLink"' in nav_link.group(0)
    assert "display:none" in nav_link.group(0)  # hidden until the server says so
    assert "d.docs_enabled ? '' : 'none'" in js
    swagger = js.index("OpenAPI (Swagger)")
    assert "${d.docs_enabled ?" in js[swagger - 400 : swagger]


def test_update_docstring_matches_behaviour() -> None:
    doc = update_memory.__doc__ or ""

    assert "enqueues the index sync" not in doc
    assert "Nothing is enqueued" in doc
