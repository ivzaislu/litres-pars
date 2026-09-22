import pytest

from litres_parser import LitResCatalog, create_catalog


def test_storage_factory_keeps_sqlite_for_explicit_local_development():
    catalog = create_catalog(sqlite_path=":memory:")
    try:
        assert isinstance(catalog, LitResCatalog)
    finally:
        catalog.close()


def test_storage_factory_rejects_non_postgres_database_url():
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        create_catalog(database_url="mysql://localhost/example")
