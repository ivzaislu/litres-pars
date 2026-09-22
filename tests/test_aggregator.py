import httpx
import pytest

from litres_parser import LitResAggregator, LitResCatalog, LitResClient


@pytest.mark.asyncio
async def test_resolve_series_fetches_once_then_serves_cache():
    calls = []

    async def handler(request: httpx.Request):
        calls.append((request.url.path, dict(request.url.params)))

        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": [
                            {
                                "instance": {
                                    "id": 42,
                                    "title": "Обреченный на бой",
                                    "art_type": 0,
                                    "persons": [
                                        {
                                            "role": "author",
                                            "full_name": "Роман Злотников",
                                        }
                                    ],
                                    "series": [],
                                }
                            }
                        ]
                    }
                },
            )

        if request.url.path.endswith("/arts/42"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": {
                            "id": 42,
                            "title": "Обреченный на бой",
                            "art_type": 0,
                            "persons": [
                                {"role": "author", "full_name": "Роман Злотников"}
                            ],
                            "series": [
                                {
                                    "id": 77,
                                    "name": "Грон",
                                    "art_order": 1,
                                }
                            ],
                        }
                    }
                },
            )

        if request.url.path.endswith("/series/77"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": {
                            "id": 77,
                            "name": "Грон",
                            "arts_count": 3,
                            "unique_arts_count": 1,
                            "parent_id": None,
                            "nested_series": [],
                        }
                    }
                },
            )

        if request.url.path.endswith("/series/77/arts"):
            assert request.url.params["show_unavailable"] == "true"
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": [
                            {
                                "id": 42,
                                "title": "Обреченный на бой",
                                "art_type": 0,
                                "persons": [
                                    {
                                        "role": "author",
                                        "full_name": "Роман Злотников",
                                    }
                                ],
                                "series": [
                                    {
                                        "id": 77,
                                        "name": "Грон",
                                        "art_order": 1,
                                    }
                                ],
                            },
                            {
                                "id": 43,
                                "title": "Обреченный на бой",
                                "art_type": 1,
                                "persons": [
                                    {
                                        "role": "author",
                                        "full_name": "Роман Злотников",
                                    }
                                ],
                                "series": [
                                    {
                                        "id": 77,
                                        "name": "Грон",
                                        "art_order": 1,
                                    }
                                ],
                            },
                            {
                                "id": 99,
                                "title": "Дочерняя книга",
                                "art_type": 0,
                                "series": [
                                    {
                                        "id": 88,
                                        "name": "Дочерний цикл",
                                        "art_order": 1,
                                    }
                                ],
                            },
                        ],
                        "pagination": {"next_page": None},
                    }
                },
            )

        raise AssertionError(f"unexpected request: {request.url}")

    with LitResCatalog(":memory:") as catalog:
        async with LitResClient(
            delay_seconds=0,
            retry_backoff_seconds=0,
            transport=httpx.MockTransport(handler),
        ) as client:
            service = LitResAggregator(client, catalog, cache_ttl_seconds=3600)

            first = await service.resolve_series(
                author="Роман Злотников",
                book_title="Обреченный на бой",
                series_name="Грон",
            )
            assert first["series_id"] == 77
            assert first["stats"] == {
                "ordered_work_count": 1,
                "direct_art_count": 2,
                "unpositioned_direct_art_count": 0,
                "expanded_art_count": 1,
            }
            assert first["works"][0]["position"] == 1
            assert [art["format"] for art in first["works"][0]["arts"]] == [
                "text",
                "audio",
            ]
            assert [art["art_id"] for art in first["expanded_arts"]] == [99]
            assert len(calls) == 4

            second = await service.resolve_series(
                author="Роман Злотников",
                book_title="Обреченный на бой",
                series_name="Грон",
            )
            assert second["series_id"] == 77
            assert len(calls) == 4


def test_series_info_keeps_multiple_audio_editions_in_one_position():
    with LitResCatalog(":memory:") as catalog:
        catalog.upsert_series(
            {
                "id": 2827,
                "name": "Хроники Сиалы",
                "arts_count": 3,
                "unique_arts_count": 1,
                "nested_series": [],
            },
            detail=True,
        )
        catalog.replace_series_arts(
            2827,
            [
                {
                    "id": 1,
                    "title": "Крадущийся в тени",
                    "art_type": 0,
                    "series": [
                        {
                            "id": 2827,
                            "name": "Хроники Сиалы",
                            "art_order": 1,
                        }
                    ],
                },
                {
                    "id": 2,
                    "title": "Крадущийся в тени",
                    "art_type": 1,
                    "series": [
                        {
                            "id": 2827,
                            "name": "Хроники Сиалы",
                            "art_order": 1,
                        }
                    ],
                },
                {
                    "id": 3,
                    "title": "Крадущийся в тени. Издание 2-е",
                    "art_type": 1,
                    "series": [
                        {
                            "id": 2827,
                            "name": "Хроники Сиалы",
                            "art_order": 1,
                        }
                    ],
                },
            ],
        )

        class NoNetwork:
            pass

        info = LitResAggregator(NoNetwork(), catalog).build_series_info(2827)

    assert info["stats"]["ordered_work_count"] == 1
    assert info["stats"]["direct_art_count"] == 3
    assert [art["format"] for art in info["works"][0]["arts"]] == [
        "text",
        "audio",
        "audio",
    ]
