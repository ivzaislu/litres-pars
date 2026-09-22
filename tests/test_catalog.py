from litres_parser import LitResCatalog


def art(art_id: int, title: str, *, position: float | None = None):
    row = {
        "id": art_id,
        "title": title,
        "art_type": 0,
        "persons": [{"role": "author", "full_name": "Тестовый Автор"}],
        "series": [],
    }
    if position is not None:
        row["series"] = [{"id": 77, "name": "Большой цикл", "art_order": position}]
    return row


def test_catalog_indexes_art_and_provider_series_claim():
    with LitResCatalog(":memory:") as catalog:
        catalog.upsert_art(art(10, "Ёжик в тумане", position=2), detail=True)

        found = catalog.find_arts("ежик в тумане")
        assert [row["art_id"] for row in found] == [10]
        assert found[0]["detail_cached"] is True

        claims = catalog.series_for_art(10)
        assert len(claims) == 1
        assert claims[0]["series_id"] == 77
        assert claims[0]["name"] == "Большой цикл"
        assert claims[0]["position"] == 2


def test_replace_series_arts_marks_complete_and_replaces_old_composition():
    with LitResCatalog(":memory:") as catalog:
        catalog.replace_series_arts(
            77,
            [art(1, "Первая", position=1), art(2, "Вторая", position=2)],
            series_detail={"id": 77, "name": "Большой цикл"},
        )
        assert catalog.get_series(77)["complete"] is True
        assert [row["art_id"] for row in catalog.series_arts(77)] == [1, 2]

        catalog.replace_series_arts(
            77,
            [art(2, "Вторая", position=2), art(3, "Третья", position=3)],
        )
        assert [row["art_id"] for row in catalog.series_arts(77)] == [2, 3]


def test_expanded_rows_do_not_become_direct_membership():
    with LitResCatalog(":memory:") as catalog:
        direct = art(1, "Прямая", position=1)
        child = {
            "id": 2,
            "title": "Дочерняя",
            "art_type": 0,
            "series": [
                {"id": 88, "name": "Дочерний цикл", "art_order": 1}
            ],
        }
        no_claim = {
            "id": 3,
            "title": "Расширенная",
            "art_type": 1,
            "series": [],
        }

        catalog.replace_series_arts(
            77,
            [direct, child, no_claim],
            series_detail={"id": 77, "name": "Большой цикл"},
        )

        assert [row["art_id"] for row in catalog.series_arts(77)] == [1]
        assert sorted(
            row["art_id"] for row in catalog.series_expanded_arts(77)
        ) == [2, 3]
        assert [row["art_id"] for row in catalog.series_arts(88)] == [2]


def test_detail_art_is_not_downgraded_by_later_card():
    with LitResCatalog(":memory:") as catalog:
        catalog.upsert_art(
            {
                "id": 10,
                "title": "Книга",
                "description": "rich detail",
                "persons": [{"role": "author", "full_name": "Автор"}],
                "series": [{"id": 77, "name": "Цикл", "art_order": 1}],
            },
            detail=True,
        )
        catalog.upsert_art(
            {
                "id": 10,
                "title": "Книга",
                "persons": [],
                "series": [],
            },
            detail=False,
        )

        row = catalog.get_art(10)
        assert row["detail_cached"] is True
        assert row["raw"]["description"] == "rich detail"
        assert row["authors"] == ["Автор"]
        assert row["series"][0]["id"] == 77


def test_series_detail_stores_hierarchy_metadata():
    with LitResCatalog(":memory:") as catalog:
        catalog.upsert_series(
            {
                "id": 77,
                "name": "Большой цикл",
                "parent_id": 7,
                "nested_series": [{"id": 88, "name": "Дочерний"}],
            },
            detail=True,
        )

        row = catalog.get_series(77)
        assert row["detail_cached"] is True
        assert row["parent_id"] == 7
        assert row["nested_series"] == [{"id": 88, "name": "Дочерний"}]


def test_sync_state_round_trip():
    with LitResCatalog(":memory:") as catalog:
        catalog.set_state("crawl", {"offset": 500, "status": "partial"})
        assert catalog.get_state("crawl") == {"offset": 500, "status": "partial"}
