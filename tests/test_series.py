import httpx
import pytest

from litres_parser import LitResCatalog, LitResClient, LitResSeriesResolver


@pytest.mark.asyncio
async def test_cold_cycle_resolution_is_two_requests_and_warm_resolution_is_zero():
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

            first = await resolver.resolve_art_cycle(42)
            assert len(first) == 1
            assert [row["art_id"] for row in first[0]["entries"]] == [41, 42]
            assert calls == [
                "/foundation/api/arts/42",
                "/foundation/api/series/77/arts",
            ]

            second = await resolver.resolve_art_cycle(42)
            assert [row["art_id"] for row in second[0]["entries"]] == [41, 42]
            assert len(calls) == 2


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
            assert await resolver.series_for_art(9) == []
            assert await resolver.series_for_art(9) == []
            assert calls == 1
