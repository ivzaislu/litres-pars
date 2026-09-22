from __future__ import annotations

from typing import Any

from .catalog import LitResCatalog
from .client import LitResClient


class LitResSeriesResolver:
    """Request-efficient access to LitRes series backed by a local catalog.

    The API is intentionally split into two network-bounded steps:

    1. discover_art_series(): art -> provider series claims (0 or 1 request)
    2. load_series(): selected series -> composition (0 requests when cached,
       otherwise only the /series/{id}/arts pagination requests)

    It never fans out over every series claim automatically.
    """

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

    async def discover_art_series(
        self,
        art_id: int,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Return LitRes series claims for one art using at most one request.

        If facets/search data already populated series_arts locally, no detail
        request is needed. Otherwise /arts/{id} is fetched once and cached.
        """
        art_id = int(art_id)
        local_claims = self.catalog.series_for_art(art_id)
        if local_claims and not refresh:
            return local_claims

        cached = self.catalog.get_art(art_id)
        if cached is not None and cached.get("detail_cached") and not refresh:
            return local_claims

        remote = await self.client.get_art(art_id)
        if remote is None:
            return []
        self.catalog.upsert_art(remote, detail=True)
        return self.catalog.series_for_art(art_id)

    async def load_series(
        self,
        series_id: int,
        *,
        refresh: bool = False,
        fetch_detail: bool = False,
    ) -> list[dict[str, Any]]:
        """Return a selected series composition, preferring the local cache.

        By default this does not call /series/{id}; the composition endpoint
        itself is sufficient to populate series membership and usually its
        provider name from member claims. Set fetch_detail=True only when the
        caller explicitly needs series-level metadata.
        """
        series_id = int(series_id)
        cached_series = self.catalog.get_series(series_id)
        if cached_series is not None and cached_series.get("complete") and not refresh:
            return self.catalog.series_arts(series_id)

        detail = None
        if fetch_detail:
            detail = await self.client.get_series(series_id)
            if detail is not None:
                self.catalog.upsert_series(detail, complete=False)

        rows = await self.client.get_series_arts(series_id)
        self.catalog.replace_series_arts(
            series_id,
            rows,
            series_detail=detail,
        )
        return self.catalog.series_arts(series_id)

    async def resolve_selected_series(
        self,
        art_id: int,
        series_id: int,
        *,
        refresh: bool = False,
    ) -> dict[str, Any] | None:
        """Resolve one explicitly selected series without request fan-out."""
        claims = await self.discover_art_series(int(art_id), refresh=refresh)
        claim = next(
            (row for row in claims if int(row["series_id"]) == int(series_id)),
            None,
        )
        if claim is None:
            return None
        entries = await self.load_series(int(series_id), refresh=refresh)
        return {
            "series": self.catalog.get_series(int(series_id)),
            "claim": claim.get("claim") or {},
            "position": claim.get("position"),
            "entries": entries,
        }

    # Compatibility aliases with explicit semantics.
    series_for_art = discover_art_series
    ensure_series = load_series
