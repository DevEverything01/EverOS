"""Console hardening: no inline handlers/styles, full ``esc()``, strict CSP.

Before: ``esc()`` left ``'`` unescaped and values were spliced into inline
``onclick="fn('${esc(x)}')"`` handlers, so a stored description such as
``x');alert(1);('`` ran script in an admin's browser.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from everos.entrypoints.api.app import create_app
from everos.entrypoints.api.routes.console import CONSOLE_CSP
from tests.fakes import make_application

STATIC = Path(__file__).resolve().parents[5] / "src" / "everos" / "web" / "static"
APP_JS = (STATIC / "app.js").read_text()
INDEX = (STATIC / "index.html").read_text()
PAYLOAD = "x');alert(1);(' \" </script><img src=x onerror=alert(2)> `${1}`"
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)


@pytest.mark.parametrize(("name", "text"), [("app.js", APP_JS), ("index", INDEX)])
def test_no_inline_event_handlers(name: str, text: str) -> None:
    assert INLINE_HANDLER.search(text) is None, name


@pytest.mark.parametrize(("name", "text"), [("app.js", APP_JS), ("index", INDEX)])
def test_no_inline_style_attributes(name: str, text: str) -> None:
    assert re.search(r"\sstyle\s*=\s*[\"']", text) is None, name


def test_index_has_no_inline_script_or_style_blocks() -> None:
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", INDEX) == []
    assert "<style" not in INDEX


def test_every_data_act_has_a_handler() -> None:
    used = set(re.findall(r'data-act="([A-Za-z]+)"', APP_JS + INDEX))
    block = APP_JS[APP_JS.index("const ACTIONS = {") :]
    block = block[: block.index("};")]
    defined = set(re.findall(r"^\s+([A-Za-z]+):", block, re.MULTILINE))
    assert used
    assert used <= defined


def _node_esc(value: str) -> str:
    line = next(ln for ln in APP_JS.splitlines() if ln.startswith("const esc = "))
    script = f"{line}\nprocess.stdout.write(esc({json.dumps(value)}));"
    return subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_esc_neutralises_quotes_and_markup() -> None:
    out = _node_esc(PAYLOAD)

    assert not set("'\"<>`") & set(out)
    assert "&#39;" in out
    assert "&lt;/script&gt;" in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_stored_description_renders_inert_in_edit_button() -> None:
    """The edit button template carries the description as an escaped attribute."""
    template = next(
        ln.strip() for ln in APP_JS.splitlines() if 'data-act="editProfile"' in ln
    )
    esc = next(ln for ln in APP_JS.splitlines() if ln.startswith("const esc = "))
    item = {"item_id": "ei_1", "category": PAYLOAD, "description": PAYLOAD}
    script = (
        f"{esc}\nconst i = {json.dumps(item)};\nprocess.stdout.write(`{template}`);"
    )
    html = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    ).stdout

    assert "<script" not in html.lower()
    assert "<img" not in html
    assert "');alert(1)" not in html
    # Every attribute value is a closed, quote-free string.
    for value in re.findall(r'data-[a-z]+="([^"]*)"', html):
        assert "'" not in value and "<" not in value


async def _console(tmp_path: Path, path: str):
    app = create_app(application=make_application(root=tmp_path))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


@pytest.mark.parametrize("path", ["/console", "/console/", "/console/app.js"])
async def test_console_responses_carry_csp(tmp_path: Path, path: str) -> None:
    response = await _console(tmp_path, path)

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CONSOLE_CSP
    assert response.headers["x-content-type-options"] == "nosniff"


def test_csp_forbids_inline_script() -> None:
    directives = dict(
        d.strip().split(" ", 1) for d in CONSOLE_CSP.split(";") if d.strip()
    )
    assert directives["default-src"] == "'self'"
    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert "unsafe-inline" not in CONSOLE_CSP
