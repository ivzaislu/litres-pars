from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from .catalog import LitResCatalog, authors_from_art, normalize_key
from .client import LitResClient
from .series import LitResSeriesResolver


class SeriesNotFoundError(LookupError):
    pass


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _position_value(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _art_format(art_type: Any) -> str:
    try:
        value = int(art_type)
    except (TypeError, ValueError):
        return "unknown"
    if value == 0:
        return "text"
    if value == 1:
        return "audio"
    return f"art_type_{value}"


def _alternative_version_ids(raw: dict[str, Any]) -> list[int]:
    value = raw.get("alternative_versions")
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            art_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if art_id not in result:
            result.append(art_id)
    return result


class LitResAggregator:
    """Cache-first LitRes series service intended to sit behind the app API."""

    def __init__(
        self,
        client: LitResClient,
        catalog: LitResCatalog,
        *,
        cache_ttl_seconds: int = 7 * 24 * 60 * 60,
        max_search_candidates: int = 3,
    ) -> None:
        self.client = client
        self.catalog = catalog
        self.resolver = LitResSeriesResolver(client, catalog)
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.max_search_candidates = max(1, int(max_search_candidates))
        self._series_locks: dict[int, asyncio.Lock] = {}
        self._resolve_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    def _series_lock(self, series_id: int) -> asyncio.Lock:
        return self._series_locks.setdefault(int(series_id), asyncio.Lock())

    def _resolve_lock(
        self, author: str, book_title: str, series_name: str
    ) -> asyncio.Lock:
        key = (
            normalize_key(author),
            normalize_key(book_title),
            normalize_key(series_name),
        )
        return self._resolve_locks.setdefault(key, asyncio.Lock())

    def _cache_ready(self, series_id: int) -> bool:
        row = self.catalog.get_series(series_id)
        if not row or not row.get("complete") or not row.get("detail_cached"):
            return False
        cached_at = _parse_datetime(row.get("cached_at"))
        if cached_at is None:
            return False
        if self.cache_ttl_seconds == 0:
            return False
        return datetime.now(timezone.utc) - cached_at <= timedelta(
            seconds=self.cache_ttl_seconds
        )

    @staticmethod
    def _author_matches(row: dict[str, Any], expected: str) -> bool:
        key = normalize_key(expected)
        return bool(key) and any(
            normalize_key(name) == key for name in authors_from_art(row)
        )

    @staticmethod
    def _title_matches(row: dict[str, Any], expected: str) -> bool:
        expected_key = normalize_key(expected)
        actual_key = normalize_key(row.get("title"))
        if not expected_key or not actual_key:
            return False
        return (
            actual_key == expected_key
            or expected_key in actual_key
            or actual_key in expected_key
        )

    def _local_series_id(
        self,
        *,
        author: str,
        book_title: str,
        series_name: str,
    ) -> int | None:
        expected_series = normalize_key(series_name)
        for art in self.catalog.find_arts(book_title, limit=30):
            if not self._title_matches(art.get("raw") or art, book_title):
                continue
            names = art.get("authors") or []
            if normalize_key(author) not in {normalize_key(name) for name in names}:
                continue
            for claim in self.catalog.series_for_art(int(art["art_id"])):
                if normalize_key(claim.get("name")) == expected_series:
                    return int(claim["series_id"])
        return None

    async def _remote_series_id(
        self,
        *,
        author: str,
        book_title: str,
        series_name: str,
    ) -> int:
        rows = await self.client.search(
            book_title,
            limit=20,
            show_unavailable=True,
        )
        candidates = [
            row
            for row in rows
            if self._author_matches(row, author) and self._title_matches(row, book_title)
        ]
        candidates.sort(
            key=lambda row: (
                normalize_key(row.get("title")) != normalize_key(book_title),
                row.get("art_type") != 0,
                int(row.get("id") or 0),
            )
        )

        expected_series = normalize_key(series_name)
        for row in candidates[: self.max_search_candidates]:
            try:
                art_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                continue
            self.catalog.upsert_art(row, detail=False)
            claims = await self.resolver.discover_art_series(art_id)
            for claim in claims:
                if normalize_key(claim.get("name")) == expected_series:
                    return int(claim["series_id"])

        raise SeriesNotFoundError(
            f"LitRes series not found for author={author!r}, "
            f"book_title={book_title!r}, series_name={series_name!r}"
        )

    async def resolve_series(
        self,
        *,
        author: str,
        book_title: str,
        series_name: str,
    ) -> dict[str, Any]:
        async with self._resolve_lock(author, book_title, series_name):
            series_id = self._local_series_id(
                author=author,
                book_title=book_title,
                series_name=series_name,
            )
            if series_id is None:
                series_id = await self._remote_series_id(
                    author=author,
                    book_title=book_title,
                    series_name=series_name,
                )
            return await self.get_series(series_id)

    async def get_series(self, series_id: int) -> dict[str, Any]:
        series_id = int(series_id)
        async with self._series_lock(series_id):
            if not self._cache_ready(series_id):
                await self.resolver.load_series(
                    series_id,
                    refresh=True,
                    fetch_detail=True,
                )
            row = self.catalog.get_series(series_id)
            if row is None:
                raise SeriesNotFoundError(f"LitRes series {series_id} not found")
            return self.build_series_info(series_id)

    @staticmethod
    def _public_art(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        return {
            "art_id": int(row["art_id"]),
            "title": row.get("title") or "",
            "art_type": row.get("art_type"),
            "format": _art_format(row.get("art_type")),
            "url": row.get("url") or "",
            "authors": list(row.get("authors") or []),
            "alternative_version_ids": _alternative_version_ids(raw),
        }

    def build_series_info(self, series_id: int) -> dict[str, Any]:
        series = self.catalog.get_series(int(series_id))
        if series is None:
            raise SeriesNotFoundError(f"LitRes series {series_id} not found")

        direct = self.catalog.series_arts(int(series_id))
        expanded = self.catalog.series_expanded_arts(int(series_id))
        grouped: dict[float, list[dict[str, Any]]] = {}
        unpositioned: list[dict[str, Any]] = []

        for row in direct:
            position = row.get("position")
            if position is None:
                unpositioned.append(self._public_art(row))
                continue
            grouped.setdefault(float(position), []).append(row)

        works: list[dict[str, Any]] = []
        for position in sorted(grouped):
            rows = grouped[position]
            rows.sort(
                key=lambda item: (
                    item.get("art_type") != 0,
                    int(item.get("art_id") or 0),
                )
            )
            public_arts = [self._public_art(row) for row in rows]
            preferred = next(
                (row for row in rows if row.get("art_type") == 0),
                rows[0],
            )
            works.append(
                {
                    "position": _position_value(position),
                    "title": preferred.get("title") or "",
                    "arts": public_arts,
                }
            )

        raw = series.get("raw") if isinstance(series.get("raw"), dict) else {}
        return {
            "source": "litres",
            "series_id": int(series["series_id"]),
            "name": series.get("name") or "",
            "parent_id": series.get("parent_id"),
            "provider_counts": {
                "arts_count": raw.get("arts_count"),
                "unique_arts_count": raw.get("unique_arts_count"),
            },
            "nested_series": list(series.get("nested_series") or []),
            "works": works,
            "unpositioned_arts": unpositioned,
            "expanded_arts": [self._public_art(row) for row in expanded],
            "stats": {
                "ordered_work_count": len(works),
                "direct_art_count": len(direct),
                "unpositioned_direct_art_count": len(unpositioned),
                "expanded_art_count": len(expanded),
            },
            "cached_at": series.get("cached_at"),
        }
