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


@pytest.mark.asyncio
async def test_resolve_series_falls_back_to_author_search_when_title_search_misses():
    calls = []

    async def handler(request: httpx.Request):
        calls.append((request.url.path, dict(request.url.params)))

        if request.url.path.endswith("/search"):
            query = request.url.params["q"]
            if query == "Книга из источника":
                return httpx.Response(
                    200,
                    json={
                        "payload": {
                            "data": [
                                {
                                    "instance": {
                                        "id": 10,
                                        "title": "Совсем другая книга",
                                        "art_type": 0,
                                        "persons": [
                                            {
                                                "role": "author",
                                                "full_name": "Нужный Автор",
                                            }
                                        ],
                                        "series": [],
                                    }
                                }
                            ]
                        }
                    },
                )
            if query == "Нужный Автор":
                return httpx.Response(
                    200,
                    json={
                        "payload": {
                            "data": [
                                {
                                    "instance": {
                                        "id": 20,
                                        "title": "Название в LitRes отличается",
                                        "art_type": 0,
                                        "persons": [
                                            {
                                                "role": "author",
                                                "full_name": "Нужный Автор",
                                            }
                                        ],
                                        "series": [],
                                    }
                                }
                            ]
                        }
                    },
                )
            raise AssertionError(f"unexpected search query: {query}")

        if request.url.path.endswith("/arts/20"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": {
                            "id": 20,
                            "title": "Название в LitRes отличается",
                            "art_type": 0,
                            "persons": [
                                {
                                    "role": "author",
                                    "full_name": "Нужный Автор",
                                }
                            ],
                            "series": [
                                {
                                    "id": 900,
                                    "name": "Нужная серия!",
                                    "art_order": 2,
                                }
                            ],
                        }
                    }
                },
            )

        if request.url.path.endswith("/series/900"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": {
                            "id": 900,
                            "name": "Нужная серия!",
                            "arts_count": 1,
                            "unique_arts_count": 1,
                            "parent_id": None,
                            "nested_series": [],
                        }
                    }
                },
            )

        if request.url.path.endswith("/series/900/arts"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": [
                            {
                                "id": 20,
                                "title": "Название в LitRes отличается",
                                "art_type": 0,
                                "persons": [
                                    {
                                        "role": "author",
                                        "full_name": "Нужный Автор",
                                    }
                                ],
                                "series": [
                                    {
                                        "id": 900,
                                        "name": "Нужная серия!",
                                        "art_order": 2,
                                    }
                                ],
                            }
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

            result = await service.resolve_series(
                author="Нужный Автор",
                book_title="Книга из источника",
                series_name="Нужная серия",
            )

    assert result["series_id"] == 900
    assert result["name"] == "Нужная серия!"
    assert result["works"][0]["position"] == 2
    assert [params.get("q") for path, params in calls if path.endswith("/search")] == [
        "Книга из источника",
        "Нужный Автор",
    ]


@pytest.mark.asyncio
async def test_author_fallback_verifies_author_from_art_detail():
    detail_calls = []

    async def handler(request: httpx.Request):
        if request.url.path.endswith("/search"):
            query = request.url.params["q"]
            if query == "Источник":
                return httpx.Response(200, json={"payload": {"data": []}})
            if query == "Автор":
                return httpx.Response(
                    200,
                    json={
                        "payload": {
                            "data": [
                                {
                                    "instance": {
                                        "id": 30,
                                        "title": "Похожее",
                                        "art_type": 0,
                                        "series": [],
                                    }
                                },
                                {
                                    "instance": {
                                        "id": 31,
                                        "title": "Нужное",
                                        "art_type": 0,
                                        "series": [],
                                    }
                                },
                            ]
                        }
                    },
                )

        if request.url.path.endswith("/arts/30"):
            detail_calls.append(30)
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": {
                            "id": 30,
                            "title": "Похожее",
                            "persons": [
                                {"role": "author", "full_name": "Другой Автор"}
                            ],
                            "series": [{"id": 901, "name": "Серия", "art_order": 1}],
                        }
                    }
                },
            )

        if request.url.path.endswith("/arts/31"):
            detail_calls.append(31)
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": {
                            "id": 31,
                            "title": "Нужное",
                            "persons": [
                                {"role": "author", "full_name": "Автор"}
                            ],
                            "series": [{"id": 902, "name": "Серия", "art_order": 1}],
                        }
                    }
                },
            )

        if request.url.path.endswith("/series/902"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": {
                            "id": 902,
                            "name": "Серия",
                            "nested_series": [],
                        }
                    }
                },
            )

        if request.url.path.endswith("/series/902/arts"):
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": [
                            {
                                "id": 31,
                                "title": "Нужное",
                                "persons": [
                                    {"role": "author", "full_name": "Автор"}
                                ],
                                "series": [{"id": 902, "name": "Серия", "art_order": 1}],
                            }
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
            result = await service.resolve_series(
                author="Автор",
                book_title="Источник",
                series_name="Серия",
            )

    assert result["series_id"] == 902
    assert detail_calls == [30, 31]
