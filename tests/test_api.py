import httpx
import pytest

from litres_parser.api import create_app


class FakeService:
    async def get_series(self, series_id: int):
        return {"series_id": series_id, "name": "Грон", "works": []}

    async def resolve_series(self, *, author: str, book_title: str, series_name: str):
        return {
            "series_id": 77,
            "name": series_name,
            "resolved_from": {
                "author": author,
                "book_title": book_title,
            },
            "works": [],
        }


@pytest.mark.asyncio
async def test_private_api_requires_bearer_token():
    app = create_app(service=FakeService(), app_token="secret")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/v1/series/77")
        assert denied.status_code == 401

        allowed = await client.get(
            "/v1/series/77",
            headers={"Authorization": "Bearer secret"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["series_id"] == 77


@pytest.mark.asyncio
async def test_private_api_resolves_series_for_app():
    app = create_app(service=FakeService(), app_token="secret")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/series/resolve",
            headers={"Authorization": "Bearer secret"},
            json={
                "author": "Роман Злотников",
                "book_title": "Обреченный на бой",
                "series_name": "Грон",
            },
        )

    assert response.status_code == 200
    assert response.json()["series_id"] == 77
    assert response.json()["name"] == "Грон"
