from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from litres_parser import LitResClient


CASES = [
    {
        "author": "Сергей Лукьяненко",
        "series_id": 728,
        "expected_name_hint": "дозор",
        "label": "Дозоры / Ночной Дозор",
        "slug": "lukyanenko_watch",
    },
    {
        "author": "Сергей Лукьяненко",
        "series_id": 1323,
        "expected_name_hint": "лабиринт",
        "label": "Лабиринт отражений",
        "slug": "lukyanenko_labyrinth",
    },
    {
        "author": "Роман Злотников",
        "series_id": 587,
        "expected_name_hint": "грон",
        "label": "Грон",
        "slug": "zlotnikov_gron",
    },
    {
        "author": "Роман Злотников",
        "series_id": 157,
        "expected_name_hint": "арвендейл",
        "label": "Арвендейл",
        "slug": "zlotnikov_arvendale",
    },
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def payload(root: Any) -> dict[str, Any]:
    if not isinstance(root, dict):
        return {}
    value = root.get("payload")
    return value if isinstance(value, dict) else {}


def payload_data(root: Any) -> Any:
    return payload(root).get("data")


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


def authors(row: dict[str, Any]) -> list[str]:
    result = []
    for person in dict_rows(row.get("persons")):
        if person.get("role") != "author":
            continue
        name = str(person.get("full_name") or "").strip()
        if name:
            result.append(name)
    return result


def series_claims(row: dict[str, Any]) -> list[dict[str, Any]]:
    return dict_rows(row.get("series"))


class Recorder:
    def __init__(self, max_requests: int) -> None:
        self.max_requests = max_requests
        self.requests: list[dict[str, Any]] = []
        self._started: dict[int, float] = {}

    async def on_request(self, request) -> None:
        if len(self.requests) >= self.max_requests:
            raise RuntimeError(f"author-series probe exceeded request budget {self.max_requests}")
        self.requests.append(
            {
                "request_no": len(self.requests) + 1,
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query),
                "status": None,
                "elapsed_ms": None,
                "response_bytes": None,
            }
        )
        self._started[id(request)] = time.perf_counter()

    async def on_response(self, response) -> None:
        await response.aread()
        started = self._started.pop(id(response.request), None)
        elapsed = ((time.perf_counter() - started) * 1000.0) if started is not None else None
        for item in reversed(self.requests):
            if item["status"] is None and item["path"] == response.request.url.path:
                item["status"] = response.status_code
                item["elapsed_ms"] = round(elapsed, 2) if elapsed is not None else None
                item["response_bytes"] = len(response.content)
                break

    def summary(self) -> dict[str, Any]:
        return {
            "total_requests": len(self.requests),
            "status_counts": dict(Counter(str(x["status"]) for x in self.requests)),
            "path_counts": dict(sorted(Counter(x["path"] for x in self.requests).items())),
            "response_bytes": sum(int(x["response_bytes"] or 0) for x in self.requests),
            "elapsed_ms": round(sum(float(x["elapsed_ms"] or 0) for x in self.requests), 2),
        }


class Probe:
    def __init__(self, out_dir: Path, max_requests: int) -> None:
        self.out_dir = out_dir
        self.raw_dir = out_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.recorder = Recorder(max_requests)

    def save(self, label: str, value: Any) -> None:
        (self.raw_dir / f"{label}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def raw_get(self, client: LitResClient, label: str, path: str, params: Any = None) -> Any:
        root = await client._get_json(path, params=params)
        self.save(label, root)
        return root

    async def probe_case(self, client: LitResClient, case: dict[str, Any]) -> dict[str, Any]:
        series_id = int(case["series_id"])
        author_n = norm(case["author"])

        detail_root = await self.raw_get(
            client,
            f"{case['slug']}_series_{series_id}",
            f"/series/{series_id}",
        )
        detail = payload_data(detail_root)
        if not isinstance(detail, dict):
            raise RuntimeError(f"Series {series_id} detail missing")

        name = str(detail.get("name") or detail.get("title") or "")
        name_matches_hint = norm(case["expected_name_hint"]) in norm(name)

        all_rows: list[dict[str, Any]] = []
        page_summaries = []
        offset = 0
        seen_offsets: set[int] = set()

        for _ in range(3):
            if offset in seen_offsets:
                raise RuntimeError(f"pagination loop for series {series_id} at {offset}")
            seen_offsets.add(offset)

            root = await self.raw_get(
                client,
                f"{case['slug']}_series_{series_id}_arts_{offset}",
                f"/series/{series_id}/arts",
                {"offset": offset, "limit": 100, "show_unavailable": "true"},
            )
            p = payload(root)
            rows = dict_rows(p.get("data"))
            pagination = p.get("pagination") if isinstance(p.get("pagination"), dict) else {}
            n_offset = next_offset(pagination.get("next_page"))

            all_rows.extend(rows)
            page_summaries.append(
                {
                    "offset": offset,
                    "rows": len(rows),
                    "next_offset": n_offset,
                }
            )

            if n_offset is None:
                break
            offset = n_offset

        direct: list[dict[str, Any]] = []
        expanded: list[dict[str, Any]] = []
        direct_positions: list[Any] = []
        direct_author_rows = 0
        direct_author_names = Counter()
        other_series_ids: set[int] = set()

        for row in all_rows:
            row_claims = series_claims(row)
            matching = [
                claim for claim in row_claims
                if int_or_none(claim.get("id")) == series_id
            ]
            if matching:
                direct.append(row)
                row_authors = authors(row)
                for a in row_authors:
                    direct_author_names[a] += 1
                if any(norm(a) == author_n for a in row_authors):
                    direct_author_rows += 1
                for claim in matching:
                    pos = claim.get("art_order")
                    if pos is None:
                        pos = claim.get("number")
                    if pos is not None:
                        direct_positions.append(pos)
            else:
                expanded.append(row)

            for claim in row_claims:
                cid = int_or_none(claim.get("id"))
                if cid is not None and cid != series_id:
                    other_series_ids.add(cid)

        nested = dict_rows(detail.get("nested_series"))
        return {
            "author": case["author"],
            "label": case["label"],
            "series_id": series_id,
            "series_name": name,
            "name_matches_hint": name_matches_hint,
            "series_detail": {
                "parent_id": detail.get("parent_id"),
                "arts_count": detail.get("arts_count"),
                "unique_arts_count": detail.get("unique_arts_count"),
                "nested_series_count": len(nested),
                "nested_series": [
                    {
                        "id": int_or_none(x.get("id")),
                        "name": x.get("name") or x.get("title"),
                        "arts_count": x.get("arts_count"),
                        "unique_arts_count": x.get("unique_arts_count"),
                    }
                    for x in nested[:30]
                ],
            },
            "composition": {
                "pages": page_summaries,
                "rows_observed": len(all_rows),
                "direct_rows": len(direct),
                "expanded_rows": len(expanded),
                "direct_ratio": round(len(direct) / len(all_rows), 4) if all_rows else 0.0,
                "direct_author_rows": direct_author_rows,
                "direct_author_ratio": round(direct_author_rows / len(direct), 4) if direct else 0.0,
                "direct_author_names": dict(direct_author_names.most_common(20)),
                "positions_observed": direct_positions[:200],
                "positions_count": len(direct_positions),
                "other_series_ids_seen": sorted(other_series_ids)[:200],
            },
        }

    async def run(self) -> dict[str, Any]:
        results = []
        started = utcnow()
        async with LitResClient(
            delay_seconds=0.5,
            retry_backoff_seconds=1.0,
            timeout_seconds=30.0,
            user_agent="litres-parser-author-series-probe/0.2 (+github.com/ivzaislu/litres-pars)",
        ) as client:
            client._client.event_hooks["request"].append(self.recorder.on_request)
            client._client.event_hooks["response"].append(self.recorder.on_response)
            for case in CASES:
                results.append(await self.probe_case(client, case))

        return {
            "started_at": started,
            "finished_at": utcnow(),
            "cases": results,
            "requests": self.recorder.summary(),
            "request_log": self.recorder.requests,
        }


async def async_main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = await Probe(out_dir, args.max_requests).run()
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    failures = []
    for case in report["cases"]:
        comp = case["composition"]
        if not case["name_matches_hint"]:
            failures.append(
                f"series {case['series_id']} unexpected name: {case['series_name']!r}"
            )
        if int(comp["rows_observed"]) == 0:
            failures.append(f"series {case['series_id']}: no rows")
        if int(comp["direct_rows"]) == 0:
            failures.append(f"series {case['series_id']}: no verified direct rows")
        if int(comp["direct_author_rows"]) == 0:
            failures.append(
                f"series {case['series_id']}: no direct rows by expected author {case['author']}"
            )

    if int(report["requests"]["total_requests"]) > args.max_requests:
        failures.append("request budget exceeded")

    if failures:
        print("HARD FAILURES:", "; ".join(failures))
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="author-series-live-results")
    parser.add_argument("--max-requests", type=int, default=24)
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
