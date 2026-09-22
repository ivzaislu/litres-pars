from __future__ import annotations

from typing import Any

from .catalog import LitResCatalog
from .client import LitResClient


class LitResSeriesResolver:
    """Request-efficient series access backed by a local LitResCatalog."""

    def __init__(self, client: LitResClient, catalog: LitResCatalog) -> None:
        self.client = client
        self.catalog = catalog

    async def ensure_art(self, art_id: int, *, refresh: bool = False) -> dict[str, Any] | None:
        cached = self.catalog.get_art(int(art_id))
        if cached is not None and cached.get("detail_cached") and not refresh:
            return cached

        remote = await self.client.get_art(int(art_id))
        if remote is None:
            return None
        self.catalog.upsert_art(remote, detail=True)
        return self.catalog.get_art(int(art_id))

    async def series_for_art(
        self,
        art_id: int,
        *,
        refresh_art: bool = False,
    ) -> list[dict[str, Any]]:
        cached = self.catalog.get_art(int(art_id))
        if cached is None or not cached.get("detail_cached") or refresh_art:
            cached = await self.ensure_art(int(art_id), refresh=refresh_art)
            if cached is None:
                return []
        return self.catalog.series_for_art(int(art_id))

    async def ensure_series(
        self,
        series_id: int,
        *,
        refresh: bool = False,
        fetch_detail: bool = False,
    ) -> list[dict[str, Any]]:
        cached_series = self.catalog.get_series(int(series_id))
        if cached_series is not None and cached_series.get("complete") and not refresh:
            return self.catalog.series_arts(int(series_id))

        detail = None
        if fetch_detail or cached_series is None or not cached_series.get("name"):
            detail = await self.client.get_series(int(series_id))
            if detail is not None:
                self.catalog.upsert_series(detail, complete=False)

        rows = await self.client.get_series_arts(int(series_id))
        self.catalog.replace_series_arts(
            int(series_id),
            rows,
            series_detail=detail,
        )
        return self.catalog.series_arts(int(series_id))

    async def resolve_art_cycle(
        self,
        art_id: int,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Return provider series claims and cached/fetched composition.

        Network cost when the cache is cold:
        * one /arts/{id} request to discover LitRes series claims;
        * one /series/{id}/arts request per series (more only if pagination needs it).

        Warm-cache calls perform zero LitRes requests.
        """
        claims = await self.series_for_art(int(art_id), refresh_art=refresh)
        result: list[dict[str, Any]] = []
        for claim in claims:
            series_id = int(claim["series_id"])
            entries = await self.ensure_series(series_id, refresh=refresh)
            result.append(
                {
                    "series": self.catalog.get_series(series_id),
                    "claim": claim.get("claim") or {},
                    "position": claim.get("position"),
                    "entries": entries,
                }
            )
        return result
