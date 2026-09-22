import httpx
import pytest

from litres_parser import LitResCatalog, LitResClient, LitResSeriesResolver


@pytest.mark.asyncio
async def test_cold_selected_series_resolution_is_two_requests_and_warm_is_zero():
    calls = []

    async def handler(request: httpx.Request):
        calls.append(request.url.path)
        if request.url.path.endswith("/arts/42"):
            return httpx.Response(
                200,
                json={"payload": {"data": {
                    "id": 42,
                    "title": "Вторая книга",
                    "art_type": 0,
                    "series": [{"id": 77, "name": "Большой цикл", "art_order": 2}],
                }}},
            )
        if request.url.path.endswith("/series/77/arts"):
            return httpx.Response(
                200,
                json={"payload": {
                    "data": [
                        {
                            "id": 41,
                            "title": "Первая книга",
                            "art_type": 0,
                            "series": [{"id": 77, "name": "Большой цикл", "art_order": 1}],
                        },
                        {
                            "id": 42,
                            "title": "Вторая книга",
                            "art_type": 0,
                            "series": [{"id": 77, "name": "Большой цикл", "art_order": 2}],
                        },
                    ],
                    "pagination": {"next_page": None},
                }},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    with LitResCatalog(":memory:") as catalog:
        async with LitResClient(
            delay_seconds=0,
            retry_backoff_seconds=0,
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = LitResSeriesResolver(client, catalog)

            claims = await resolver.discover_art_series(42)
            assert [row["series_id"] for row in claims] == [77]
            entries = await resolver.load_series(77)
            assert [row["art_id"] for row in entries] == [41, 42]
            assert calls == [
                "/foundation/api/arts/42",
                "/foundation/api/series/77/arts",
            ]

            claims2 = await resolver.discover_art_series(42)
            entries2 = await resolver.load_series(77)
            assert [row["series_id"] for row in claims2] == [77]
            assert [row["art_id"] for row in entries2] == [41, 42]
            assert len(calls) == 2


@pytest.mark.asyncio
async def test_existing_local_series_claim_avoids_art_detail_request():
    calls = 0

    async def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        raise AssertionError(f"network should not be used: {request.url}")

    with LitResCatalog(":memory:") as catalog:
        catalog.upsert_art(
            {
                "id": 42,
                "title": "Книга",
                "series": [{"id": 77, "name": "Цикл", "art_order": 3}],
            },
            detail=False,
        )
        async with LitResClient(
            delay_seconds=0,
            retry_backoff_seconds=0,
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = LitResSeriesResolver(client, catalog)
            claims = await resolver.discover_art_series(42)

    assert [row["series_id"] for row in claims] == [77]
    assert calls == 0


@pytest.mark.asyncio
async def test_cached_detail_without_series_does_not_repeat_request():
    calls = 0

    async def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"payload": {"data": {"id": 9, "title": "Одиночная", "series": []}}},
        )

    with LitResCatalog(":memory:") as catalog:
        async with LitResClient(
            delay_seconds=0,
            retry_backoff_seconds=0,
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = LitResSeriesResolver(client, catalog)
            assert await resolver.discover_art_series(9) == []
            assert await resolver.discover_art_series(9) == []
            assert calls == 1


@pytest.mark.asyncio
async def test_multiple_series_claims_do_not_trigger_automatic_fanout():
    paths = []

    async def handler(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path.endswith("/arts/50"):
            return httpx.Response(
                200,
                json={"payload": {"data": {
                    "id": 50,
                    "title": "Книга",
                    "series": [
                        {"id": 1, "name": "Цикл A", "art_order": 1},
                        {"id": 2, "name": "Коллекция B", "art_order": 10},
                        {"id": 3, "name": "Серия C"},
                    ],
                }}},
            )
        raise AssertionError(f"unexpected fan-out request: {request.url}")

    with LitResCatalog(":memory:") as catalog:
        async with LitResClient(
            delay_seconds=0,
            retry_backoff_seconds=0,
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = LitResSeriesResolver(client, catalog)
            claims = await resolver.discover_art_series(50)

    assert [row["series_id"] for row in claims] == [1, 2, 3]
    assert paths == ["/foundation/api/arts/50"]
