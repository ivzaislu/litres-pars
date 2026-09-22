import os

import pytest

from litres_parser import PostgresCatalog, create_catalog


DATABASE_URL = os.environ.get("POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="POSTGRES_TEST_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
def catalog():
    instance = PostgresCatalog(DATABASE_URL)
    with instance.db.transaction():
        with instance.db.cursor() as cur:
            cur.execute(
                """
                TRUNCATE TABLE
                    series_expanded_arts,
                    series_arts,
                    arts,
                    series,
                    sync_state
                CASCADE
                """
            )
    try:
        yield instance
    finally:
        instance.close()


def _art(art_id, title, *, art_type=0, position=None, series_id=77):
    series = []
    if series_id is not None:
        claim = {"id": series_id, "name": "Большой цикл"}
        if position is not None:
            claim["art_order"] = position
        series = [claim]
    return {
        "id": art_id,
        "title": title,
        "art_type": art_type,
        "persons": [{"role": "author", "full_name": "Тестовый Автор"}],
        "series": series,
    }


def test_postgres_factory_and_schema_are_ready():
    instance = create_catalog(database_url=DATABASE_URL)
    try:
        assert isinstance(instance, PostgresCatalog)
        assert instance.ping() is True
        assert instance.stats() == {
            "arts": 0,
            "series": 0,
            "series_memberships": 0,
            "series_expanded_rows": 0,
            "complete_series": 0,
        }
    finally:
        instance.close()


def test_postgres_preserves_direct_expanded_and_multiple_formats(catalog):
    catalog.upsert_series(
        {
            "id": 77,
            "name": "Большой цикл",
            "parent_id": 7,
            "nested_series": [{"id": 88, "name": "Дочерний цикл"}],
            "arts_count": 4,
            "unique_arts_count": 2,
        },
        detail=True,
    )

    rows = [
        _art(1, "Первая", art_type=0, position=1),
        _art(2, "Первая", art_type=1, position=1),
        _art(3, "Первая. Другая аудиоверсия", art_type=1, position=1),
        _art(4, "Дочерняя", art_type=0, position=1, series_id=88),
    ]

    catalog.replace_series_arts(77, rows)

    direct = catalog.series_arts(77)
    expanded = catalog.series_expanded_arts(77)
    series = catalog.get_series(77)

    assert [row["art_id"] for row in direct] == [1, 2, 3]
    assert [row["position"] for row in direct] == [1.0, 1.0, 1.0]
    assert [row["art_type"] for row in direct] == [0, 1, 1]
    assert [row["art_id"] for row in expanded] == [4]

    assert series["complete"] is True
    assert series["detail_cached"] is True
    assert series["parent_id"] == 7
    assert series["nested_series"] == [{"id": 88, "name": "Дочерний цикл"}]

    # The child row remains a real direct member of its own provider series.
    assert [row["art_id"] for row in catalog.series_arts(88)] == [4]


def test_postgres_rich_art_detail_is_not_downgraded(catalog):
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


def test_postgres_state_round_trip(catalog):
    catalog.set_state("refresh", {"series_id": 77, "status": "ok"})
    assert catalog.get_state("refresh") == {"series_id": 77, "status": "ok"}
