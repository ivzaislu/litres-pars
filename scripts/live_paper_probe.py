from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from litres_parser import LitResClient


CASES = [
    {"author": "Сергей Лукьяненко", "title": "Ночной Дозор", "series_id": 728, "slug": "night_watch"},
    {"author": "Сергей Лукьяненко", "title": "Лабиринт отражений", "series_id": 1323, "slug": "labyrinth"},
    {"author": "Роман Злотников", "title": "Обреченный на бой", "series_id": 587, "slug": "gron_1"},
    {"author": "Роман Злотников", "title": "Арвендейл", "series_id": 157, "slug": "arvendale_1"},
]


def norm(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def payload(root: Any) -> dict[str, Any]:
    if not isinstance(root, dict):
        return {}
    p = root.get("payload")
    return p if isinstance(p, dict) else {}


def data(root: Any) -> Any:
    return payload(root).get("data")


def rows(value: Any) -> list[dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def unwrap(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    nested = row.get("instance")
    return nested if isinstance(nested, dict) else row


def authors(row: dict[str, Any]) -> list[str]:
    out = []
    for p in rows(row.get("persons")):
        if p.get("role") == "author" and p.get("full_name"):
            out.append(str(p["full_name"]))
    return out


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Recorder:
    def __init__(self, max_requests: int) -> None:
        self.max_requests = max_requests
        self.log: list[dict[str, Any]] = []
        self.started: dict[int, float] = {}

    async def on_request(self, request) -> None:
        if len(self.log) >= self.max_requests:
            raise RuntimeError(f"paper probe exceeded request budget {self.max_requests}")
        self.log.append({
            "no": len(self.log) + 1,
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
        elapsed = (time.perf_counter() - started) * 1000 if started is not None else None
        for item in reversed(self.log):
            if item["status"] is None and item["path"] == response.request.url.path:
                item["status"] = response.status_code
                item["elapsed_ms"] = round(elapsed, 2) if elapsed is not None else None
                item["bytes"] = len(response.content)
                break

    def summary(self) -> dict[str, Any]:
        return {
            "total_requests": len(self.log),
            "status_counts": dict(Counter(str(x["status"]) for x in self.log)),
            "response_bytes": sum(int(x["bytes"] or 0) for x in self.log),
        }


async def raw_get(client: LitResClient, out: Path, label: str, path: str, params: Any = None) -> Any:
    root = await client._get_json(path, params=params)
    (out / f"{label}.json").write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


async def main_async(args: argparse.Namespace) -> int:
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(args.max_requests)
    report_cases = []

    async with LitResClient(
        delay_seconds=0.5,
        retry_backoff_seconds=1.0,
        timeout_seconds=30.0,
        user_agent="litres-parser-paper-probe/0.1 (+github.com/ivzaislu/litres-pars)",
    ) as client:
        client._client.event_hooks["request"].append(recorder.on_request)
        client._client.event_hooks["response"].append(recorder.on_response)

        for case in CASES:
            series_root = await raw_get(
                client, raw, f"{case['slug']}_series_arts",
                f"/series/{case['series_id']}/arts",
                {"offset": 0, "limit": 100, "show_unavailable": "true"},
            )
            series_rows = rows(data(series_root))
            series_ids = {int_or_none(r.get("id")) for r in series_rows}
            series_ids.discard(None)

            search_root = await raw_get(
                client, raw, f"{case['slug']}_paper_search",
                "/search",
                [
                    ("q", case["title"]),
                    ("limit", 20),
                    ("offset", 0),
                    ("o", "popular"),
                    ("show_unavailable", "true"),
                    ("types", "paper_book"),
                ],
            )
            search_rows = [x for raw_row in rows(data(search_root)) if (x := unwrap(raw_row)) is not None]
            author_n = norm(case["author"])
            title_n = norm(case["title"])
            matches = []
            for row in search_rows:
                row_authors = authors(row)
                same_author = any(norm(a) == author_n for a in row_authors)
                row_title_n = norm(row.get("title"))
                title_match = row_title_n == title_n or title_n in row_title_n or row_title_n in title_n
                if same_author and title_match:
                    matches.append({
                        "id": int_or_none(row.get("id")),
                        "title": row.get("title"),
                        "art_type": row.get("art_type"),
                        "authors": row_authors,
                        "keys": sorted(row.keys()),
                        "alternative_versions": row.get("alternative_versions"),
                        "alternative_version": row.get("alternative_version"),
                        "linked_arts": row.get("linked_arts"),
                        "synchronized_arts": row.get("synchronized_arts"),
                        "in_series_response": int_or_none(row.get("id")) in series_ids,
                    })

            report_cases.append({
                **case,
                "series_rows": len(series_rows),
                "series_art_type_counts": dict(Counter(str(r.get("art_type")) for r in series_rows)),
                "paper_search_rows": len(search_rows),
                "paper_matches": matches,
                "paper_match_ids": [m["id"] for m in matches],
                "paper_ids_in_series_response": [m["id"] for m in matches if m["in_series_response"]],
            })

    report = {
        "cases": report_cases,
        "requests": recorder.summary(),
        "request_log": recorder.log,
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    failures = []
    if recorder.summary()["total_requests"] > args.max_requests:
        failures.append("request budget exceeded")
    if failures:
        print("HARD FAILURES:", "; ".join(failures))
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="paper-live-results")
    p.add_argument("--max-requests", type=int, default=12)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
