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


def test_sync_state_round_trip():
    with LitResCatalog(":memory:") as catalog:
        catalog.set_state("crawl", {"offset": 500, "status": "partial"})
        assert catalog.get_state("crawl") == {"offset": 500, "status": "partial"}
