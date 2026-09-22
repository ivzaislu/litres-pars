from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import LitResCatalog
from .client import LitResClient


def _facet_values(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    facets = payload.get("facets")
    if not isinstance(facets, list):
        return []
    for facet in facets:
        if not isinstance(facet, dict) or facet.get("name") != name:
            continue
        data = facet.get("data")
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    return []


def _leaf_genres(nodes: Any) -> list[dict[str, Any]]:
    if not isinstance(nodes, list):
        return []
    result: dict[int, dict[str, Any]] = {}

    def walk(items: list[Any]) -> None:
        for row in items:
            if not isinstance(row, dict):
                continue
            children = row.get("subgenres")
            if isinstance(children, list) and children:
                walk(children)
                continue
            try:
                genre_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            result.setdefault(
                genre_id,
                {
                    "id": genre_id,
                    "name": str(row.get("name") or row.get("title") or ""),
                    "token": str(row.get("token") or ""),
                },
            )

    walk(nodes)
    return sorted(result.values(), key=lambda row: int(row["id"]))


@dataclass(slots=True)
class CrawlResult:
    status: str
    fetched: int
    stored: int
    segment_index: int
    segments_total: int
    offset: int


class LitResCatalogCrawler:
    """Resumable /arts/facets crawler that writes raw provider data to SQLite."""

    STATE_KEY = "facets_segmented_v1"

    def __init__(self, client: LitResClient, catalog: LitResCatalog) -> None:
        self.client = client
        self.catalog = catalog

    async def discover_plan(self) -> list[dict[str, Any]]:
        base = await self.client.get_facets_page(limit=1)
        art_types = [
            str(row.get("value") or "")
            for row in _facet_values(base.payload, "art_types")
            if str(row.get("value") or "").strip()
        ]
        languages = [
            str(row.get("value") or "")
            for row in _facet_values(base.payload, "languages")
            if str(row.get("value") or "").strip()
        ]

        if not art_types or not languages:
            raise RuntimeError("LitRes facets did not expose art_types/languages for safe segmentation")

        plan: list[dict[str, Any]] = []
        for art_type in sorted(set(art_types)):
            if art_type != "text_book":
                plan.append(
                    {
                        "path": "/arts/facets",
                        "filters": {"art_types": art_type},
                        "label": art_type,
                    }
                )

        for language in sorted(set(languages)):
            if language != "ru":
                plan.append(
                    {
                        "path": "/arts/facets",
                        "filters": {"art_types": "text_book", "languages": language},
                        "label": f"text_book/{language}",
                    }
                )

        if "ru" in languages and "text_book" in art_types:
            genres = await self.client.get_genres()
            leaves = _leaf_genres(genres)
            if leaves:
                for genre in leaves:
                    plan.append(
                        {
                            "path": f"/genres/{int(genre['id'])}/arts/facets",
                            "filters": {"art_types": "text_book", "languages": "ru"},
                            "label": genre.get("name") or genre.get("token") or str(genre["id"]),
                        }
                    )
            else:
                raise RuntimeError("LitRes genre tree returned no leaf genres for safe Russian text segmentation")

        if not plan:
            raise RuntimeError("LitRes segmented crawl plan is empty")
        return plan

    async def run_chunk(
        self,
        *,
        max_rows: int = 100_000,
        restart: bool = False,
    ) -> CrawlResult:
        budget = max(1, int(max_rows))
        state = None if restart else self.catalog.get_state(self.STATE_KEY)

        if not state or state.get("status") == "complete":
            state = {
                "status": "running",
                "plan": await self.discover_plan(),
                "segment_index": 0,
                "offset": 0,
                "fetched": 0,
                "stored": 0,
            }
            self.catalog.set_state(self.STATE_KEY, state)

        plan = state.get("plan")
        if not isinstance(plan, list) or not plan:
            raise RuntimeError("LitRes crawl plan is empty")

        segment_index = max(0, int(state.get("segment_index") or 0))
        offset = max(0, int(state.get("offset") or 0))
        fetched = max(0, int(state.get("fetched") or 0))
        stored = max(0, int(state.get("stored") or 0))
        fetched_this_run = 0

        while segment_index < len(plan) and fetched_this_run < budget:
            segment = plan[segment_index]
            path = str(segment.get("path") or "")
            filters = segment.get("filters")
            if not path.startswith("/"):
                raise RuntimeError(f"Invalid LitRes crawl path: {path!r}")
            if not isinstance(filters, dict):
                filters = {}

            page = await self.client.get_facets_page(
                path=path,
                filters=filters,
                offset=offset,
            )
            page_rows = page.rows
            fetched += len(page_rows)
            fetched_this_run += len(page_rows)
            stored += self.catalog.upsert_arts(page_rows)

            if page.next_offset is None:
                segment_index += 1
                offset = 0
            else:
                if page.next_offset <= offset:
                    raise RuntimeError(
                        f"LitRes facets pagination did not advance: offset={offset}, next={page.next_offset}"
                    )
                offset = page.next_offset

            state = {
                "status": "running",
                "plan": plan,
                "segment_index": segment_index,
                "offset": offset,
                "fetched": fetched,
                "stored": stored,
            }
            self.catalog.set_state(self.STATE_KEY, state)

        status = "complete" if segment_index >= len(plan) else "partial"
        state["status"] = status
        self.catalog.set_state(self.STATE_KEY, state)
        return CrawlResult(
            status=status,
            fetched=fetched,
            stored=stored,
            segment_index=segment_index,
            segments_total=len(plan),
            offset=offset,
        )
