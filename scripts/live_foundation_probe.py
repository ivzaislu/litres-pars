from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from litres_parser import LitResClient


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def payload(root: Any) -> dict[str, Any]:
    if not isinstance(root, dict):
        return {}
    value = root.get("payload")
    return value if isinstance(value, dict) else {}


def payload_data(root: Any) -> Any:
    return payload(root).get("data")


def unwrap_instance(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    nested = row.get("instance")
    return nested if isinstance(nested, dict) else row


def dict_rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def next_offset(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        raw = parse_qs(urlparse(value).query).get("offset", [None])[0]
        result = int(raw)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def key_union(rows: list[dict[str, Any]]) -> list[str]:
    keys: set[str] = set()
    for row in rows:
        keys.update(str(key) for key in row)
    return sorted(keys)


def series_claims(row: dict[str, Any]) -> list[dict[str, Any]]:
    return dict_rows(row.get("series"))


def series_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    claims = [claim for row in rows for claim in series_claims(row)]
    with_field = sum("series" in row for row in rows)
    nonempty = sum(bool(series_claims(row)) for row in rows)
    return {
        "rows": len(rows),
        "rows_with_series_field": with_field,
        "rows_with_nonempty_series": nonempty,
        "series_claim_keys": key_union(claims),
    }


def leaf_genres(nodes: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(items: Any) -> None:
        if not isinstance(items, list):
            return
        for row in items:
            if not isinstance(row, dict):
                continue
            children = row.get("subgenres")
            if isinstance(children, list) and children:
                walk(children)
            else:
                result.append(row)

    walk(nodes)
    return result


class Recorder:
    def __init__(self, out_dir: Path, max_requests: int) -> None:
        self.out_dir = out_dir
        self.max_requests = max_requests
        self.requests: list[dict[str, Any]] = []
        self._started: dict[int, float] = {}

    async def on_request(self, request) -> None:
        if len(self.requests) >= self.max_requests:
            raise RuntimeError(f"live probe request budget exceeded: {self.max_requests}")
        item = {
            "request_no": len(self.requests) + 1,
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query),
            "started_at": utcnow(),
            "status": None,
            "elapsed_ms": None,
            "response_bytes": None,
        }
        self.requests.append(item)
        self._started[id(request)] = time.perf_counter()

    async def on_response(self, response) -> None:
        await response.aread()
        request = response.request
        started = self._started.pop(id(request), None)
        elapsed = ((time.perf_counter() - started) * 1000.0) if started is not None else None
        for item in reversed(self.requests):
            if item["status"] is None and item["path"] == request.url.path:
                item["status"] = response.status_code
                item["elapsed_ms"] = round(elapsed, 2) if elapsed is not None else None
                item["response_bytes"] = len(response.content)
                break

    def summary(self) -> dict[str, Any]:
        by_path = Counter(item["path"] for item in self.requests)
        by_status = Counter(str(item["status"]) for item in self.requests)
        repeated = sum(count - 1 for count in by_path.values() if count > 1)
        return {
            "total_requests": len(self.requests),
            "requests_by_path": dict(sorted(by_path.items())),
            "responses_by_status": dict(sorted(by_status.items())),
            "repeated_path_requests": repeated,
            "total_response_bytes": sum(int(item["response_bytes"] or 0) for item in self.requests),
            "total_elapsed_ms": round(sum(float(item["elapsed_ms"] or 0) for item in self.requests), 2),
        }


class LiveProbe:
    def __init__(self, out_dir: Path, max_requests: int = 30) -> None:
        self.out_dir = out_dir
        self.raw_dir = out_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.recorder = Recorder(out_dir, max_requests)
        self.report: dict[str, Any] = {
            "started_at": utcnow(),
            "max_requests": max_requests,
            "endpoints": {},
            "hypotheses": {},
            "warnings": [],
        }

    def save_raw(self, label: str, value: Any) -> None:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label).strip("_")
        (self.raw_dir / f"{safe}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def get(self, client: LitResClient, label: str, path: str, params: Any = None) -> Any:
        root = await client._get_json(path, params=params)
        self.save_raw(label, root)
        return root

    async def run(self) -> dict[str, Any]:
        async with LitResClient(
            delay_seconds=0.45,
            retry_backoff_seconds=1.0,
            timeout_seconds=30.0,
            user_agent="litres-parser-live-probe/0.1 (+github.com/ivzaislu/litres-pars)",
        ) as client:
            client._client.event_hooks["request"].append(self.recorder.on_request)
            client._client.event_hooks["response"].append(self.recorder.on_response)

            await self.probe_search(client)
            art_ids = await self.probe_arts(client)
            await self.probe_facets(client, art_ids)
            await self.probe_series(client)
            await self.probe_genres(client)

        self.report["requests"] = self.recorder.summary()
        self.report["request_log"] = self.recorder.requests
        self.report["finished_at"] = utcnow()
        self.derive_hypotheses()
        return self.report

    async def probe_search(self, client: LitResClient) -> None:
        params = [
            ("q", "Метро 2033"),
            ("limit", 10),
            ("offset", 0),
            ("o", "popular"),
            ("show_unavailable", "false"),
            ("types", "text_book"),
            ("types", "audiobook"),
            ("types", "paper_book"),
        ]
        root = await self.get(client, "search_metro_2033", "/search", params)
        rows = [row for raw in dict_rows(payload_data(root)) if (row := unwrap_instance(raw)) is not None]
        self.report["endpoints"]["search"] = {
            **series_stats(rows),
            "item_keys": key_union(rows),
            "sample_ids": [int_or_none(row.get("id")) for row in rows[:10]],
        }

    async def probe_arts(self, client: LitResClient) -> list[int]:
        candidates: list[int] = [128391, 56420524]
        search_ids = [
            value for value in self.report["endpoints"]["search"]["sample_ids"]
            if isinstance(value, int)
        ]
        candidates.extend(search_ids[:2])
        seen: set[int] = set()
        results = []
        working_ids: list[int] = []

        for art_id in candidates:
            if art_id in seen:
                continue
            seen.add(art_id)
            root = await self.get(client, f"art_{art_id}", f"/arts/{art_id}")
            row = payload_data(root)
            ok = isinstance(row, dict)
            if ok:
                working_ids.append(art_id)
            results.append(
                {
                    "art_id": art_id,
                    "found": ok,
                    "keys": sorted(row.keys()) if ok else [],
                    **(series_stats([row]) if ok else series_stats([])),
                }
            )
            if len(results) >= 4:
                break

        self.report["endpoints"]["arts_detail"] = {
            "tested": results,
            "working_ids": working_ids,
        }
        return working_ids

    async def probe_facets(self, client: LitResClient, art_ids: list[int]) -> None:
        discovery = await self.get(
            client,
            "facets_discovery",
            "/arts/facets",
            {"offset": 0, "limit": 1},
        )
        discovery_payload = payload(discovery)
        facets = dict_rows(discovery_payload.get("facets"))
        facet_names = [str(row.get("name") or "") for row in facets]
        facet_values: dict[str, list[str]] = {}
        for facet in facets:
            name = str(facet.get("name") or "")
            values = dict_rows(facet.get("data"))
            facet_values[name] = [str(item.get("value") or "") for item in values[:30]]

        root = await self.get(
            client,
            "facets_text_ru",
            "/arts/facets",
            {"art_types": "text_book", "languages": "ru", "offset": 0, "limit": 50},
        )
        p = payload(root)
        rows = dict_rows(p.get("data"))
        pagination = p.get("pagination") if isinstance(p.get("pagination"), dict) else {}
        counters = p.get("counters") if isinstance(p.get("counters"), dict) else {}

        comparisons = []
        detail_budget = 3
        rows_for_detail = [row for row in rows if series_claims(row)] or rows
        for row in rows_for_detail[:detail_budget]:
            art_id = int_or_none(row.get("id"))
            if art_id is None:
                continue
            detail_root = await self.get(client, f"facets_compare_art_{art_id}", f"/arts/{art_id}")
            detail = payload_data(detail_root)
            if not isinstance(detail, dict):
                continue
            facet_ids = sorted(
                value for claim in series_claims(row)
                if (value := int_or_none(claim.get("id"))) is not None
            )
            detail_ids = sorted(
                value for claim in series_claims(detail)
                if (value := int_or_none(claim.get("id"))) is not None
            )
            comparisons.append(
                {
                    "art_id": art_id,
                    "facets_series_ids": facet_ids,
                    "detail_series_ids": detail_ids,
                    "same_series_ids": facet_ids == detail_ids,
                    "facets_series_claim_keys": key_union(series_claims(row)),
                    "detail_series_claim_keys": key_union(series_claims(detail)),
                }
            )

        self.report["endpoints"]["facets"] = {
            "discovery_facet_names": sorted(name for name in facet_names if name),
            "discovery_values": facet_values,
            **series_stats(rows),
            "item_keys": key_union(rows),
            "counter_all": int_or_none(counters.get("all")),
            "next_offset": next_offset(pagination.get("next_page")),
            "detail_comparisons": comparisons,
        }

    async def _probe_series_arts_page(
        self,
        client: LitResClient,
        series_id: int,
        *,
        label: str,
        show_unavailable: bool,
        follow_one_next_page: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"offset": 0, "limit": 100}
        if show_unavailable:
            params["show_unavailable"] = "true"
        first_root = await self.get(
            client,
            f"{label}_page_0",
            f"/series/{series_id}/arts",
            params,
        )
        p = payload(first_root)
        rows = dict_rows(p.get("data"))
        pagination = p.get("pagination") if isinstance(p.get("pagination"), dict) else {}
        n_offset = next_offset(pagination.get("next_page"))
        second_rows: list[dict[str, Any]] = []

        if follow_one_next_page and n_offset is not None:
            params2: dict[str, Any] = {"offset": n_offset, "limit": 100}
            if show_unavailable:
                params2["show_unavailable"] = "true"
            second_root = await self.get(
                client,
                f"{label}_page_{n_offset}",
                f"/series/{series_id}/arts",
                params2,
            )
            second_rows = dict_rows(payload_data(second_root))

        all_rows = rows + second_rows
        direct_claims = 0
        position_fields = Counter()
        claim_keys: set[str] = set()
        for row in all_rows:
            matching = []
            for claim in series_claims(row):
                claim_keys.update(str(key) for key in claim)
                if int_or_none(claim.get("id")) == series_id:
                    matching.append(claim)
            if matching:
                direct_claims += 1
                for claim in matching:
                    if claim.get("art_order") is not None:
                        position_fields["art_order"] += 1
                    if claim.get("number") is not None:
                        position_fields["number"] += 1

        return {
            "first_page_rows": len(rows),
            "second_page_rows": len(second_rows),
            "rows_observed": len(all_rows),
            "next_offset": n_offset,
            "direct_claim_rows": direct_claims,
            "rows_without_requested_claim": len(all_rows) - direct_claims,
            "direct_claim_ratio": round(direct_claims / len(all_rows), 4) if all_rows else 0.0,
            "position_fields_seen": dict(position_fields),
            "member_item_keys": key_union(all_rows),
            "member_series_claim_keys": sorted(claim_keys),
        }

    async def probe_series(self, client: LitResClient) -> None:
        series_id = None
        source_art_id = None

        details = self.report["endpoints"]["arts_detail"]["tested"]
        for tested in details:
            if not tested.get("found"):
                continue
            art_id = int(tested["art_id"])
            raw = json.loads((self.raw_dir / f"art_{art_id}.json").read_text(encoding="utf-8"))
            row = payload_data(raw)
            if not isinstance(row, dict):
                continue
            claims = series_claims(row)
            if claims:
                series_id = int_or_none(claims[0].get("id"))
                source_art_id = art_id
                if series_id is not None:
                    break

        if series_id is None:
            series_id = 1505
            self.report["warnings"].append(
                "No series claim found in tested art details; falling back to historical series_id=1505"
            )

        detail_root = await self.get(client, f"series_{series_id}", f"/series/{series_id}")
        detail = payload_data(detail_root)
        nested = dict_rows(detail.get("nested_series")) if isinstance(detail, dict) else []

        normal = await self._probe_series_arts_page(
            client,
            series_id,
            label=f"series_{series_id}_arts_default",
            show_unavailable=False,
            follow_one_next_page=True,
        )
        including_unavailable = await self._probe_series_arts_page(
            client,
            series_id,
            label=f"series_{series_id}_arts_with_unavailable",
            show_unavailable=True,
            follow_one_next_page=True,
        )

        nested_results = []
        for nested_row in nested[:2]:
            nested_id = int_or_none(nested_row.get("id"))
            if nested_id is None:
                continue
            nested_detail_root = await self.get(
                client,
                f"series_{nested_id}",
                f"/series/{nested_id}",
            )
            nested_detail = payload_data(nested_detail_root)
            nested_arts = await self._probe_series_arts_page(
                client,
                nested_id,
                label=f"series_{nested_id}_arts_with_unavailable",
                show_unavailable=True,
                follow_one_next_page=True,
            )
            nested_results.append(
                {
                    "series_id": nested_id,
                    "name": nested_row.get("name"),
                    "parent_id": nested_detail.get("parent_id") if isinstance(nested_detail, dict) else None,
                    "arts_count": nested_detail.get("arts_count") if isinstance(nested_detail, dict) else None,
                    "unique_arts_count": nested_detail.get("unique_arts_count") if isinstance(nested_detail, dict) else None,
                    "nested_series_count": len(dict_rows(nested_detail.get("nested_series"))) if isinstance(nested_detail, dict) else None,
                    "arts_probe": nested_arts,
                }
            )

        self.report["endpoints"]["series"] = {
            "source_art_id": source_art_id,
            "series_id": series_id,
            "detail_found": isinstance(detail, dict),
            "detail_keys": sorted(detail.keys()) if isinstance(detail, dict) else [],
            "arts_count": detail.get("arts_count") if isinstance(detail, dict) else None,
            "unique_arts_count": detail.get("unique_arts_count") if isinstance(detail, dict) else None,
            "parent_id": detail.get("parent_id") if isinstance(detail, dict) else None,
            "nested_series": [
                {
                    "id": int_or_none(row.get("id")),
                    "name": row.get("name"),
                    "arts_count": row.get("arts_count"),
                    "unique_arts_count": row.get("unique_arts_count"),
                }
                for row in nested
            ],
            "default_composition": normal,
            "composition_with_unavailable": including_unavailable,
            "nested_probes": nested_results,
        }

    async def probe_genres(self, client: LitResClient) -> None:
        root = await self.get(
            client,
            "genres",
            "/genres",
            {"art_group": 1, "api_version": 2},
        )
        data = payload_data(root)
        leaves = leaf_genres(data)
        result: dict[str, Any] = {
            "root_type": type(data).__name__,
            "leaf_count": len(leaves),
            "leaf_keys": key_union(leaves[:100]),
        }
        if leaves:
            genre_id = int_or_none(leaves[0].get("id"))
            result["sample_leaf_id"] = genre_id
            result["sample_leaf_name"] = leaves[0].get("name") or leaves[0].get("title")
            if genre_id is not None:
                sample = await self.get(
                    client,
                    f"genre_{genre_id}_facets",
                    f"/genres/{genre_id}/arts/facets",
                    {"art_types": "text_book", "languages": "ru", "offset": 0, "limit": 10},
                )
                p = payload(sample)
                rows = dict_rows(p.get("data"))
                pagination = p.get("pagination") if isinstance(p.get("pagination"), dict) else {}
                result["facets"] = {
                    **series_stats(rows),
                    "item_keys": key_union(rows),
                    "next_offset": next_offset(pagination.get("next_page")),
                }
        self.report["endpoints"]["genres"] = result

    def derive_hypotheses(self) -> None:
        search = self.report["endpoints"].get("search", {})
        facets = self.report["endpoints"].get("facets", {})
        arts = self.report["endpoints"].get("arts_detail", {})
        series = self.report["endpoints"].get("series", {})

        detail_series = any(
            int(row.get("rows_with_nonempty_series") or 0) > 0
            for row in arts.get("tested", [])
        )
        comparisons = facets.get("detail_comparisons") or []
        comparable = [row for row in comparisons if row.get("detail_series_ids")]
        exact = [row for row in comparable if row.get("same_series_ids")]

        self.report["hypotheses"] = {
            "search_exposes_series_field": int(search.get("rows_with_series_field") or 0) > 0,
            "search_exposes_nonempty_series": int(search.get("rows_with_nonempty_series") or 0) > 0,
            "art_detail_exposes_nonempty_series": detail_series,
            "facets_exposes_series_field": int(facets.get("rows_with_series_field") or 0) > 0,
            "facets_exposes_nonempty_series": int(facets.get("rows_with_nonempty_series") or 0) > 0,
            "facets_matches_detail_series_for_all_compared": bool(comparable) and len(exact) == len(comparable),
            "series_arts_rows_all_carry_requested_series_claim": (
                int((series.get("composition_with_unavailable") or {}).get("rows_observed") or 0) > 0
                and int((series.get("composition_with_unavailable") or {}).get("rows_without_requested_claim") or 0) == 0
            ),
            "series_arts_expands_beyond_direct_membership": (
                int((series.get("composition_with_unavailable") or {}).get("rows_without_requested_claim") or 0) > 0
            ),
            "show_unavailable_changes_series_result_count": (
                int((series.get("default_composition") or {}).get("rows_observed") or 0)
                != int((series.get("composition_with_unavailable") or {}).get("rows_observed") or 0)
            ),
            "series_detail_is_not_required_to_fetch_composition": (
                int((series.get("composition_with_unavailable") or {}).get("rows_observed") or 0) > 0
            ),
        }


async def async_main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = LiveProbe(out_dir, max_requests=args.max_requests)
    report = await probe.run()
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    hard_failures = []
    requests = report.get("requests", {})
    if int(requests.get("total_requests") or 0) > args.max_requests:
        hard_failures.append("request budget exceeded")
    if not report.get("endpoints", {}).get("facets", {}).get("item_keys"):
        hard_failures.append("facets returned no parsable rows")
    if not report.get("endpoints", {}).get("arts_detail", {}).get("working_ids"):
        hard_failures.append("no tested art detail was available")
    if int(
        (report.get("endpoints", {}).get("series", {}).get("composition_with_unavailable") or {}).get("rows_observed") or 0
    ) == 0:
        hard_failures.append("series composition returned no rows")

    if hard_failures:
        print("HARD FAILURES:", "; ".join(hard_failures))
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="live-results")
    parser.add_argument("--max-requests", type=int, default=30)
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
