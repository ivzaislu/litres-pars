from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

import httpx

DEFAULT_API_BASE_URL = "https://api.litres.ru/foundation/api"
DEFAULT_WEB_BASE_URL = "https://www.litres.ru"
MAX_SERIES_PAGE_SIZE = 100
MAX_FACETS_PAGE_SIZE = 500
RETRY_STATUSES = {429, 500, 502, 503, 504}

Params = Mapping[str, Any] | Sequence[tuple[str, Any]] | None


@dataclass(slots=True)
class LitResFacetsPage:
    rows: list[dict[str, Any]]
    next_offset: int | None
    total: int | None
    payload: dict[str, Any]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _next_offset(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parse_qs(urlparse(value).query)
        raw = parsed.get("offset", [None])[0]
        result = int(raw)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _instance(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    nested = row.get("instance")
    return nested if isinstance(nested, dict) else row


class LitResClient:
    """Async client/parser for the LitRes Foundation API.

    This package deliberately contains no Abred-specific identity, matching,
    database, reconciliation or canonical-work rules. Methods expose LitRes
    provider data with only transport/pagination unwrapping.
    """

    def __init__(
        self,
        *,
        api_base_url: str = DEFAULT_API_BASE_URL,
        delay_seconds: float = 0.35,
        timeout_seconds: float = 20.0,
        user_agent: str = "litres-parser/0.1",
        language: str = "ru",
        retry_backoff_seconds: float = 0.75,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=self.api_base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            transport=transport,
            headers={
                "Accept": "application/json",
                "Accept-Version": "2",
                "ui-language-code": language,
                "User-Agent": user_agent,
            },
        )

    async def __aenter__(self) -> "LitResClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        if self.delay_seconds <= 0:
            return
        async with self._lock:
            wait = self.delay_seconds - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _get_json(self, path: str, *, params: Params = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(4):
            await self._throttle()
            try:
                response = await self._client.get(path, params=params)
                if response.status_code == 404:
                    return None
                if response.status_code in RETRY_STATUSES and attempt < 3:
                    await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt < 3:
                    await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))
                    continue
        raise RuntimeError(f"LitRes request failed for {path}: {last_exc}")

    @staticmethod
    def payload(root: Any) -> dict[str, Any] | None:
        if not isinstance(root, dict):
            return None
        payload = root.get("payload")
        return payload if isinstance(payload, dict) else None

    @classmethod
    def payload_data(cls, root: Any) -> Any:
        payload = cls.payload(root)
        return payload.get("data") if payload is not None else None

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        types: Sequence[str] = ("text_book", "audiobook", "paper_book"),
        show_unavailable: bool = False,
        order: str = "popular",
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [
            ("q", query),
            ("limit", max(1, min(50, int(limit)))),
            ("offset", max(0, int(offset))),
            ("o", order),
            ("show_unavailable", "true" if show_unavailable else "false"),
        ]
        params.extend(("types", value) for value in types)
        data = self.payload_data(await self._get_json("/search", params=params))
        if not isinstance(data, list):
            return []
        result: list[dict[str, Any]] = []
        for raw in data:
            row = _instance(raw)
            if row is not None:
                result.append(row)
        return result

    async def get_art(self, art_id: int) -> dict[str, Any] | None:
        data = self.payload_data(await self._get_json(f"/arts/{int(art_id)}"))
        return data if isinstance(data, dict) else None

    async def get_series(self, series_id: int) -> dict[str, Any] | None:
        data = self.payload_data(await self._get_json(f"/series/{int(series_id)}"))
        return data if isinstance(data, dict) else None

    async def get_series_arts(
        self,
        series_id: int,
        *,
        limit: int = MAX_SERIES_PAGE_SIZE,
        max_pages: int | None = None,
        show_unavailable: bool = False,
    ) -> list[dict[str, Any]]:
        page_size = max(1, min(MAX_SERIES_PAGE_SIZE, int(limit)))
        result: list[dict[str, Any]] = []
        offset = 0
        pages = 0
        seen_offsets: set[int] = set()

        while True:
            if max_pages is not None and pages >= max(0, int(max_pages)):
                break
            if offset in seen_offsets:
                raise RuntimeError(f"LitRes series pagination loop detected at offset={offset}")
            seen_offsets.add(offset)

            root = await self._get_json(
                f"/series/{int(series_id)}/arts",
                params={
                    "limit": page_size,
                    "offset": offset,
                    "show_unavailable": "true" if show_unavailable else "false",
                },
            )
            payload = self.payload(root)
            if payload is None:
                break
            data = payload.get("data")
            rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
            result.extend(rows)
            pages += 1

            pagination = payload.get("pagination")
            next_page = pagination.get("next_page") if isinstance(pagination, dict) else None
            next_offset = _next_offset(next_page)
            if next_offset is None:
                break
            offset = next_offset

        return result

    async def get_similar_arts(
        self,
        art_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        root = await self._get_json(
            f"/arts/{int(art_id)}/similar",
            params={"limit": max(1, min(50, int(limit))), "offset": max(0, int(offset))},
        )
        data = self.payload_data(root)
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    async def get_facets_page(
        self,
        *,
        offset: int = 0,
        limit: int = MAX_FACETS_PAGE_SIZE,
        filters: Mapping[str, Any] | None = None,
        path: str = "/arts/facets",
    ) -> LitResFacetsPage:
        page_size = max(1, min(MAX_FACETS_PAGE_SIZE, int(limit)))
        params = dict(filters or {})
        params.update({"offset": max(0, int(offset)), "limit": page_size})
        root = await self._get_json(path, params=params)
        payload = self.payload(root) or {}
        data = payload.get("data")
        rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        pagination = payload.get("pagination")
        counters = payload.get("counters")
        next_page = pagination.get("next_page") if isinstance(pagination, dict) else None
        total = _int_or_none(counters.get("all")) if isinstance(counters, dict) else None
        return LitResFacetsPage(
            rows=rows,
            next_offset=_next_offset(next_page),
            total=total,
            payload=payload,
        )

    async def get_genres(
        self,
        *,
        art_group: int = 1,
        api_version: int = 2,
    ) -> Any:
        return self.payload_data(
            await self._get_json(
                "/genres",
                params={"art_group": int(art_group), "api_version": int(api_version)},
            )
        )

    async def get_genre_facets_page(
        self,
        genre_id: int,
        *,
        offset: int = 0,
        limit: int = MAX_FACETS_PAGE_SIZE,
        filters: Mapping[str, Any] | None = None,
    ) -> LitResFacetsPage:
        return await self.get_facets_page(
            offset=offset,
            limit=limit,
            filters=filters,
            path=f"/genres/{int(genre_id)}/arts/facets",
        )
