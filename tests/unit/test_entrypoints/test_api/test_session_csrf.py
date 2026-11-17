"""A session-cookie write must come from the server's own origin.

The console cookie is ``SameSite=Lax``, and a ``multipart/form-data`` POST is
a "simple" request (no preflight): a page on a sibling subdomain or another
port of the same host could upload knowledge documents with the user's
cookie. Before, nothing checked where such a request came from. Requests
authenticated by an ``X-Api-Key`` / ``Authorization`` header are not checked:
a browser never adds those headers by itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from .conftest import SecuredApp

UPLOAD = "/api/v2/knowledge/documents"
EVIL = "http://evil.test"
SAME = "http://test"


@pytest.fixture
def knowledge(secured: SecuredApp) -> SimpleNamespace:
    knowledge = secured.app.state.application.knowledge
    knowledge.create_from_upload.return_value = SimpleNamespace(
        doc_id="doc_1",
        category_id="cat_1",
        topic_count=1,
        source_name="a.md",
        md_path="knowledge/doc_1.md",
        original_file_path=None,
    )
    return knowledge


def _upload_kwargs(**headers: str) -> dict:
    return {
        "files": {"file": ("a.md", b"# Notes\n\nhello", "text/markdown")},
        "data": {"title": "Notes"},
        "headers": headers,
    }


async def _session_upload(secured: SecuredApp, **headers: str):
    async with secured.client() as client:
        login = await client.post(
            "/api/v2/auth/session", json={"key": secured.write_key}
        )
        assert login.status_code == 200
        return await client.post(UPLOAD, **_upload_kwargs(**headers))


async def test_cookie_upload_from_another_origin_is_403(
    secured: SecuredApp, knowledge: SimpleNamespace
) -> None:
    response = await _session_upload(secured, Origin=EVIL)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CROSS_SITE_REFUSED"
    knowledge.create_from_upload.assert_not_awaited()


@pytest.mark.parametrize(
    "headers",
    [
        {"Referer": f"{EVIL}/page"},
        {"Origin": "null"},
        {"Origin": "http://test:8080"},
        {"Origin": "http://sub.test"},
    ],
)
async def test_cookie_write_cross_site_variants_are_403(
    secured: SecuredApp, knowledge: SimpleNamespace, headers: dict[str, str]
) -> None:
    response = await _session_upload(secured, **headers)

    assert response.status_code == 403
    knowledge.create_from_upload.assert_not_awaited()


@pytest.mark.parametrize(
    "headers", [{"Origin": SAME}, {"Referer": f"{SAME}/console/"}, {}]
)
async def test_cookie_upload_from_own_origin_is_200(
    secured: SecuredApp, knowledge: SimpleNamespace, headers: dict[str, str]
) -> None:
    response = await _session_upload(secured, **headers)

    assert response.status_code == 201
    knowledge.create_from_upload.assert_awaited_once()


@pytest.mark.parametrize("header", ["X-Api-Key", "Authorization"])
async def test_key_header_upload_ignores_origin(
    secured: SecuredApp, knowledge: SimpleNamespace, header: str
) -> None:
    value = (
        secured.write_key if header == "X-Api-Key" else f"Bearer {secured.write_key}"
    )
    async with secured.client() as client:
        response = await client.post(
            UPLOAD, **_upload_kwargs(Origin=EVIL, **{header: value})
        )

    assert response.status_code == 201
    knowledge.create_from_upload.assert_awaited_once()


async def test_cookie_read_from_another_origin_is_allowed(
    secured: SecuredApp,
) -> None:
    """Safe methods change nothing; CORS governs whether a page may read them."""
    async with secured.client() as client:
        await client.post("/api/v2/auth/session", json={"key": secured.write_key})
        response = await client.get(
            "/api/v2/memory/tags?memory_id=m1", headers={"Origin": EVIL}
        )

    assert response.status_code == 200
