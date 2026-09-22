import httpx
import pytest

from litres_parser import LitResClient


@pytest.mark.asyncio
async def test_search_unwraps_instance():
    async def handler(request: httpx.Request):
        assert request.url.path.endswith("/search")
        assert request.url.params.get_list("types") == ["text_book", "audiobook", "paper_book"]
        return httpx.Response(
            200,
            json={"payload": {"data": [{"instance": {"id": 42, "title": "Книга"}}]}},
        )

    async with LitResClient(delay_seconds=0, transport=httpx.MockTransport(handler)) as client:
        rows = await client.search("Книга")

    assert rows == [{"id": 42, "title": "Книга"}]


@pytest.mark.asyncio
async def test_series_pagination_follows_server_next_page():
    offsets = []

    async def handler(request: httpx.Request):
        offset = int(request.url.params.get("offset", "0"))
        offsets.append(offset)
        if offset == 0:
            return httpx.Response(
                200,
                json={"payload": {
                    "data": [{"id": 1}],
                    "pagination": {
                        "next_page": "https://api.litres.ru/foundation/api/series/7/arts?offset=37&limit=100"
                    },
                }},
            )
        return httpx.Response(
            200,
            json={"payload": {"data": [{"id": 2}], "pagination": {"next_page": None}}},
        )

    async with LitResClient(delay_seconds=0, transport=httpx.MockTransport(handler)) as client:
        rows = await client.get_series_arts(7)

    assert [row["id"] for row in rows] == [1, 2]
    assert offsets == [0, 37]


@pytest.mark.asyncio
async def test_facets_page_parses_pagination_and_total():
    async def handler(request: httpx.Request):
        assert request.url.path.endswith("/arts/facets")
        return httpx.Response(
            200,
            json={"payload": {
                "data": [{"id": 1}],
                "pagination": {"next_page": "/foundation/api/arts/facets?offset=500&limit=500"},
                "counters": {"all": 1234},
            }},
        )

    async with LitResClient(delay_seconds=0, transport=httpx.MockTransport(handler)) as client:
        page = await client.get_facets_page()

    assert page.rows == [{"id": 1}]
    assert page.next_offset == 500
    assert page.total == 1234


@pytest.mark.asyncio
async def test_similar_endpoint_returns_provider_rows():
    async def handler(request: httpx.Request):
        assert request.url.path.endswith("/arts/10/similar")
        return httpx.Response(200, json={"payload": {"data": [{"id": 11}, {"id": 12}]}})

    async with LitResClient(delay_seconds=0, transport=httpx.MockTransport(handler)) as client:
        rows = await client.get_similar_arts(10)

    assert rows == [{"id": 11}, {"id": 12}]
