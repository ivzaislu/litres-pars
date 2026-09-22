from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from .catalog import LitResCatalog


@runtime_checkable
class CatalogRepository(Protocol):
    """Storage contract shared by SQLite development and PostgreSQL production."""

    def close(self) -> None: ...

    def upsert_art(
        self,
        row: dict[str, Any],
        *,
        detail: bool = False,
        commit: bool = True,
    ) -> bool: ...

    def upsert_arts(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        detail: bool = False,
    ) -> int: ...

    def upsert_series(
        self,
        row: dict[str, Any],
        *,
        complete: bool | None = None,
        detail: bool = False,
        commit: bool = True,
    ) -> bool: ...

    def replace_series_arts(
        self,
        series_id: int,
        rows: Iterable[dict[str, Any]],
        *,
        series_detail: dict[str, Any] | None = None,
    ) -> int: ...

    def get_art(self, art_id: int) -> dict[str, Any] | None: ...
    def find_arts(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]: ...
    def find_series(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]: ...
    def get_series(self, series_id: int) -> dict[str, Any] | None: ...
    def series_for_art(self, art_id: int) -> list[dict[str, Any]]: ...
    def series_arts(self, series_id: int) -> list[dict[str, Any]]: ...
    def series_expanded_arts(self, series_id: int) -> list[dict[str, Any]]: ...
    def set_state(self, key: str, value: dict[str, Any]) -> None: ...
    def get_state(self, key: str) -> dict[str, Any] | None: ...
    def stats(self) -> dict[str, int]: ...


def create_catalog(
    *,
    database_url: str | None = None,
    sqlite_path: str | Path | None = None,
) -> CatalogRepository:
    """Create the configured catalog backend.

    Northflank production should provide DATABASE_URL from the PostgreSQL
    addon. SQLite remains available only when an explicit local path is given.
    """
    url = database_url or os.environ.get("DATABASE_URL")
    if url:
        if not url.startswith(("postgres://", "postgresql://")):
            raise RuntimeError("DATABASE_URL must be a PostgreSQL URL")
        from .postgres_catalog import PostgresCatalog

        return PostgresCatalog(url)

    path = sqlite_path or os.environ.get("LITRES_DB_PATH")
    if path:
        return LitResCatalog(path)

    raise RuntimeError(
        "DATABASE_URL is required for server mode; "
        "set LITRES_DB_PATH only for local SQLite development"
    )
