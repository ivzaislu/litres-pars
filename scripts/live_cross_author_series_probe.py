from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from litres_parser import LitResClient


CASES = [
    {"author": "Макс Фрай", "series_id": 1324, "name_hint": "Лабиринты Ехо", "slug": "max_frei_labyrinths"},
    {"author": "Вадим Панов", "series_id": 2560, "name_hint": "Тайный Город", "slug": "panov_secret_city"},
    {"author": "Алексей Пехов", "series_id": 2827, "name_hint": "Хроники Сиалы", "slug": "pehov_siala"},
    {"author": "Сергей Тармашев", "series_id": 11402, "name_hint": "Древний", "slug": "tarmashev_ancient"},
    {"author": "Борис Акунин", "series_id": 2025, "name_hint": "Фандорин", "slug": "akunin_fandorin"},
]


def payload(root: Any) -> dict[str, Any]:
    if not isinstance(root, dict):
        return {}
    value = root.get("payload")
    return value if isinstance(value, dict) else {}


def data(root: Any) -> Any:
    return payload(root).get("data")


def rows(value: Any) -> list[dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


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
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def claims(row: dict[str, Any]) -> list[dict[str, Any]]:
    return rows(row.get("series"))


def authors(row: dict[str, Any]) -> list[str]:
    return [
        str(p.get("full_name"))
        for p in rows(row.get("persons"))
        if p.get("role") == "author" and p.get("full_name")
    ]


def position_for(row: dict[str, Any], series_id: int) -> Any:
    for claim in claims(row):
        if int_or_none(claim.get("id")) != series_id:
            continue
        value = claim.get("art_order")
        if value is None:
            value = claim.get("number")
        return value
    return None


class Recorder:
    def __init__(self, max_requests: int) -> None:
        self.max_requests = max_requests
        self.items: list[dict[str, Any]] = []
        self.started: dict[int, float] = {}

    async def on_request(self, request) -> None:
        if len(self.items) >= self.max_requests:
            raise RuntimeError(f"cross-author probe exceeded request budget {self.max_requests}")
        self.items.append({
            "no": len(self.items) + 1,
            "path": request.url.path,
            "query": str(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query),
            "status": None,
            "elapsed_ms": None,
            "bytes": None,
        })
        self.started[id(request)] = time.perf_counter()

    async def on_response(self, response) -> None:
        await response.aread()
        started = self.started.pop(id(response.request), None)
        elapsed = ((time.perf_counter() - started) * 1000.0) if started is not None else None
        for item in reversed(self.items):
            if item["status"] is None and item["path"] == response.request.url.path:
                item["status"] = response.status_code
                item["elapsed_ms"] = round(elapsed, 2) if elapsed is not None else None
                item["bytes"] = len(response.content)
                break

    def summary(self) -> dict[str, Any]:
        return {
            "total_requests": len(self.items),
            "status_counts": dict(Counter(str(x["status"]) for x in self.items)),
            "response_bytes": sum(int(x["bytes"] or 0) for x in self.items),
            "elapsed_ms": round(sum(float(x["elapsed_ms"] or 0) for x in self.items), 2),
        }


class Probe:
    def __init__(self, out: Path, max_requests: int) -> None:
        self.out = out
        self.raw = out / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.recorder = Recorder(max_requests)

    async def get(self, client: LitResClient, label: str, path: str, params: Any = None) -> Any:
        root = await client._get_json(path, params=params)
        (self.raw / f"{label}.json").write_text(
            json.dumps(root, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return root

    async def probe_case(self, client: LitResClient, case: dict[str, Any]) -> dict[str, Any]:
        series_id = int(case["series_id"])
        detail_root = await self.get(client, f"{case['slug']}_detail", f"/series/{series_id}")
        detail = data(detail_root)
        if not isinstance(detail, dict):
            raise RuntimeError(f"series {series_id} detail missing")

        all_rows: list[dict[str, Any]] = []
        page_summaries = []
        offset = 0
        seen_offsets: set[int] = set()

        for _ in range(4):
            if offset in seen_offsets:
                raise RuntimeError(f"pagination loop series={series_id} offset={offset}")
            seen_offsets.add(offset)
            root = await self.get(
                client,
                f"{case['slug']}_arts_{offset}",
                f"/series/{series_id}/arts",
                {"offset": offset, "limit": 100, "show_unavailable": "true"},
            )
            p = payload(root)
            page_rows = rows(p.get("data"))
            pagination = p.get("pagination") if isinstance(p.get("pagination"), dict) else {}
            n_offset = next_offset(pagination.get("next_page"))
            all_rows.extend(page_rows)
            page_summaries.append({"offset": offset, "rows": len(page_rows), "next_offset": n_offset})
            if n_offset is None:
                break
            offset = n_offset

        direct = []
        expanded = []
        by_id: dict[int, dict[str, Any]] = {}
        position_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        art_types = Counter()
        author_rows = 0

        for row in all_rows:
            matching = [c for c in claims(row) if int_or_none(c.get("id")) == series_id]
            if not matching:
                expanded.append(row)
                continue

            direct.append(row)
            art_id = int_or_none(row.get("id"))
            if art_id is not None:
                by_id[art_id] = row
            art_types[str(row.get("art_type"))] += 1
            if case["author"] in authors(row):
                author_rows += 1

            pos = position_for(row, series_id)
            if pos is not None:
                position_groups[str(pos)].append(row)

        alternative_pairs = []
        seen_pairs: set[tuple[int, int]] = set()
        for row in direct:
            left_id = int_or_none(row.get("id"))
            if left_id is None:
                continue
            left_pos = position_for(row, series_id)
            for alt in rows(row.get("alternative_versions")):
                right_id = int_or_none(alt.get("id"))
                if right_id is None or right_id not in by_id:
                    continue
                key = tuple(sorted((left_id, right_id)))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                right = by_id[right_id]
                right_pos = position_for(right, series_id)
                reciprocal = any(
                    int_or_none(back.get("id")) == left_id
                    for back in rows(right.get("alternative_versions"))
                )
                alternative_pairs.append({
                    "left_id": left_id,
                    "right_id": right_id,
                    "left_type": row.get("art_type"),
                    "right_type": right.get("art_type"),
                    "left_title": row.get("title"),
                    "right_title": right.get("title"),
                    "left_position": left_pos,
                    "right_position": right_pos,
                    "same_position": left_pos is not None and left_pos == right_pos,
                    "different_type": row.get("art_type") != right.get("art_type"),
                    "reciprocal": reciprocal,
                    "link_type": alt.get("link_type"),
                })

        proven_pairs = [
            p for p in alternative_pairs
            if p["same_position"] and p["different_type"] and p["reciprocal"]
        ]

        group_summary = []
        multi_format_positions = 0
        one_text_many_audio_positions = 0
        for pos, group in sorted(position_groups.items(), key=lambda kv: float(kv[0])):
            type_counts = Counter(str(x.get("art_type")) for x in group)
            ids = [int_or_none(x.get("id")) for x in group]
            if len(type_counts) > 1:
                multi_format_positions += 1
            if int(type_counts.get("0", 0)) >= 1 and int(type_counts.get("1", 0)) >= 2:
                one_text_many_audio_positions += 1
            group_summary.append({
                "position": pos,
                "rows": len(group),
                "art_type_counts": dict(sorted(type_counts.items())),
                "art_ids": ids,
                "titles": [x.get("title") for x in group],
            })

        nested = rows(detail.get("nested_series"))
        return {
            "author": case["author"],
            "series_id": series_id,
            "series_name": detail.get("name") or detail.get("title"),
            "detail": {
                "arts_count": detail.get("arts_count"),
                "unique_arts_count": detail.get("unique_arts_count"),
                "parent_id": detail.get("parent_id"),
                "nested_series_count": len(nested),
            },
            "composition": {
                "pages": page_summaries,
                "rows": len(all_rows),
                "direct_rows": len(direct),
                "expanded_rows": len(expanded),
                "art_type_counts": dict(sorted(art_types.items())),
                "expected_author_rows": author_rows,
                "ordered_positions": len(position_groups),
                "multi_format_positions": multi_format_positions,
                "one_text_many_audio_positions": one_text_many_audio_positions,
                "position_groups": group_summary,
                "alternative_pairs_total": len(alternative_pairs),
                "proven_cross_format_pairs": len(proven_pairs),
                "proven_pair_positions": sorted(
                    {str(p["left_position"]) for p in proven_pairs if p["left_position"] is not None},
                    key=float,
                ),
                "proven_pairs": proven_pairs,
            },
        }

    async def run(self) -> dict[str, Any]:
        results = []
        async with LitResClient(
            delay_seconds=0.5,
            retry_backoff_seconds=1.0,
            timeout_seconds=30.0,
            user_agent="litres-parser-cross-author-series-probe/0.1 (+github.com/ivzaislu/litres-pars)",
        ) as client:
            client._client.event_hooks["request"].append(self.recorder.on_request)
            client._client.event_hooks["response"].append(self.recorder.on_response)
            for case in CASES:
                results.append(await self.probe_case(client, case))

        return {
            "cases": results,
            "requests": self.recorder.summary(),
            "request_log": self.recorder.items,
        }


async def main_async(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = await Probe(out, args.max_requests).run()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    failures = []
    for case in report["cases"]:
        comp = case["composition"]
        if int(comp["direct_rows"]) == 0:
            failures.append(f"{case['series_id']}: no direct rows")
        if int(comp["expected_author_rows"]) == 0:
            failures.append(f"{case['series_id']}: expected author absent")
        if int((comp["art_type_counts"] or {}).get("0") or 0) == 0:
            failures.append(f"{case['series_id']}: no text rows")
        if int((comp["art_type_counts"] or {}).get("1") or 0) == 0:
            failures.append(f"{case['series_id']}: no audio rows")
        if int(comp["multi_format_positions"]) == 0:
            failures.append(f"{case['series_id']}: no position contains multiple formats")
        if int(comp["proven_cross_format_pairs"]) == 0:
            failures.append(f"{case['series_id']}: no reciprocal cross-format alternative pair")

    if int(report["requests"]["total_requests"]) > args.max_requests:
        failures.append("request budget exceeded")

    if failures:
        print("HARD FAILURES:", "; ".join(failures))
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="cross-author-series-results")
    p.add_argument("--max-requests", type=int, default=20)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
