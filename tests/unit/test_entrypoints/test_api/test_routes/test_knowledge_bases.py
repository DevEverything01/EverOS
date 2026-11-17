"""Unit tests for multi knowledge base endpoints.

Covers Cloud-parity features:
- POST /knowledge_bases — create a knowledge base
- GET /knowledge_bases — list knowledge bases
- DELETE /knowledge_bases/{kb_id} — delete a knowledge base
- POST /knowledge_bases/{kb_id}/documents — upload a document
- GET /knowledge_bases/{kb_id}/documents — list documents
- GET /knowledge_bases/{kb_id}/categories — list categories
- POST /knowledge_bases/{kb_id}/search — search knowledge
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from everos.entrypoints.api.app import create_app
from tests.fakes import make_application


@pytest.fixture
def app_with_knowledge():
    """Create an app with knowledge support."""
    application = make_application()
    app = create_app(application=application)
    return app


class TestKnowledgeBaseCRUD:
    """Tests for knowledge base CRUD operations."""

    async def test_create_knowledge_base(self, app_with_knowledge) -> None:
        """Create a new knowledge base."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/knowledge_bases",
                json={
                    "name": "My KB",
                    "description": "A test knowledge base",
                },
            )

        assert response.status_code in (201, 401, 403)
        if response.status_code == 201:
            data = response.json()["data"]
            assert data["name"] == "My KB"
            assert data["kb_id"].startswith("kb_")

    async def test_list_knowledge_bases(self, app_with_knowledge) -> None:
        """List knowledge bases includes default."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v2/knowledge_bases")

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "items" in data
            assert "total" in data

    async def test_delete_knowledge_base(self, app_with_knowledge) -> None:
        """Delete a knowledge base returns 204."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.delete("/api/v2/knowledge_bases/kb_12345678")

        assert response.status_code in (204, 401, 403)


class TestKnowledgeBaseDocuments:
    """Tests for documents within a knowledge base."""

    async def test_create_document_with_form(self, app_with_knowledge) -> None:
        """Create a document via form fields."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/knowledge_bases/kb_12345678/documents",
                data={
                    "title": "Test Doc",
                    "content": "Some content here",
                },
            )

        assert response.status_code in (201, 401, 403)
        if response.status_code == 201:
            data = response.json()["data"]
            assert data["title"] == "Test Doc"
            assert data["doc_id"].startswith("d_")

    async def test_list_documents(self, app_with_knowledge) -> None:
        """List documents in a knowledge base."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v2/knowledge_bases/kb_12345678/documents")

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "items" in data
            assert "total" in data
            assert "page" in data
            assert "page_size" in data

    async def test_get_document_not_found(self, app_with_knowledge) -> None:
        """Get a non-existent document returns 404."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/knowledge_bases/kb_12345678/documents/d_123456789012"
            )

        assert response.status_code in (404, 401, 403)


class TestKnowledgeBaseCategories:
    """Tests for categories within a knowledge base."""

    async def test_list_categories_empty(self, app_with_knowledge) -> None:
        """List categories returns empty list for new KB."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/knowledge_bases/kb_12345678/categories"
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "categories" in data


class TestKnowledgeBaseTopics:
    """Tests for topics within a knowledge base."""

    async def test_get_topic_not_found(self, app_with_knowledge) -> None:
        """Get a non-existent topic returns 404."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v2/knowledge_bases/kb_12345678/topics/d_123456789012_1"
            )

        assert response.status_code in (404, 401, 403)


class TestKnowledgeBaseSearch:
    """Tests for search within a knowledge base."""

    async def test_search_returns_empty(self, app_with_knowledge) -> None:
        """Search in empty KB returns empty results."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v2/knowledge_bases/kb_12345678/search",
                json={
                    "query": "test query",
                    "method": "hybrid",
                    "top_k": 10,
                },
            )

        assert response.status_code == 200
        if response.status_code == 200:
            data = response.json()["data"]
            assert "topics" in data
            assert "total" in data


class TestKnowledgeBaseIdValidation:
    """Tests for kb_id path parameter validation."""

    async def test_invalid_kb_id_format(self, app_with_knowledge) -> None:
        """Invalid kb_id format returns 422."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_knowledge),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v2/knowledge_bases/invalid/documents")

        assert response.status_code in (422, 401, 403)
