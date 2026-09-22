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
        "work": "Ночной Дозор",
        "series_hint": "дозор",
        "slug": "lukyanenko_night_watch",
    },
    {
        "author": "Сергей Лукьяненко",
        "work": "Лабиринт отражений",
        "series_hint": "лабиринт",
        "slug": "lukyanenko_labyrinth",
    },
    {
        "author": "Роман Злотников",
        "work": "Грон",
        "series_hint": "грон",
        "slug": "zlotnikov_gron",
    },
    {
        "author": "Роман Злотников",
        "work": "Арвендейл",
        "series_hint": "арвендейл",
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


def unwrap_instance(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    nested = row.get("instance")
    return nested if isinstance(nested, dict) else row


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


def choose_series(claims: list[dict[str, Any]], hint: str) -> dict[str, Any] | None:
    hint_n = norm(hint)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, claim in enumerate(claims):
        name = str(claim.get("name") or claim.get("title") or "")
        name_n = norm(name)
        score = 0
        if hint_n and hint_n in name_n:
            score += 100
        if claim.get("art_order") is not None or claim.get("number") is not None:
            score += 20
        if int_or_none(claim.get("id")) is not None:
            score += 5
        ranked.append((score, -index, claim))
    if not ranked:
        return None
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return ranked[0][2]


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

    async def find_art(self, client: LitResClient, case: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
        search_root = await self.raw_get(
            client,
            f"{case['slug']}_search",
            "/search",
            [
                ("q", case["work"]),
                ("limit", 10),
                ("offset", 0),
                ("o", "popular"),
                ("show_unavailable", "true"),
                ("types", "text_book"),
                ("types", "audiobook"),
                ("types", "paper_book"),
            ],
        )
        search_rows = [
            row for raw in dict_rows(payload_data(search_root))
            if (row := unwrap_instance(raw)) is not None
        ]

        author_n = norm(case["author"])
        work_n = norm(case["work"])
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in search_rows:
            title_n = norm(row.get("title"))
            names = authors(row)
            score = 0
            if title_n == work_n:
                score += 100
            elif work_n in title_n or title_n in work_n:
                score += 40
            if any(author_n == norm(name) for name in names):
                score += 100
            ranked.append((score, row))
        ranked.sort(reverse=True, key=lambda item: item[0])

        tried = []
        for score, row in ranked[:4]:
            art_id = int_or_none(row.get("id"))
            if art_id is None:
                continue
            detail_root = await self.raw_get(
                client,
                f"{case['slug']}_art_{art_id}",
                f"/arts/{art_id}",
            )
            detail = payload_data(detail_root)
            if not isinstance(detail, dict):
                tried.append({"art_id": art_id, "reason": "detail_missing"})
                continue
            detail_authors = authors(detail)
            same_author = any(author_n == norm(name) for name in detail_authors)
            claims = series_claims(detail)
            tried.append(
                {
                    "art_id": art_id,
                    "search_score": score,
                    "title": detail.get("title"),
                    "authors": detail_authors,
                    "same_author": same_author,
                    "series_count": len(claims),
                }
            )
            if same_author and claims:
                return detail, {"search_rows": len(search_rows), "tried": tried}

        raise RuntimeError(
            f"Could not find {case['author']} / {case['work']} with non-empty series claims"
        )

    async def probe_series(self, client: LitResClient, case: dict[str, str]) -> dict[str, Any]:
        art, discovery = await self.find_art(client, case)
        claims = series_claims(art)
        selected = choose_series(claims, case["series_hint"])
        if selected is None:
            raise RuntimeError(f"No series claim for {case['work']}")
        series_id = int_or_none(selected.get("id"))
        if series_id is None:
            raise RuntimeError(f"Chosen series has no id for {case['work']}")

        detail_root = await self.raw_get(
            client,
            f"{case['slug']}_series_{series_id}",
            f"/series/{series_id}",
        )
        detail = payload_data(detail_root)
        if not isinstance(detail, dict):
            raise RuntimeError(f"Series {series_id} detail missing")

        all_rows: list[dict[str, Any]] = []
        offset = 0
        pages = 0
        seen_offsets: set[int] = set()
        page_summaries = []

        while pages < 3:
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
            page_summaries.append({"offset": offset, "rows": len(rows), "next_offset": n_offset})
            pages += 1
            if n_offset is None:
                break
            offset = n_offset

        direct = []
        expanded = []
        positions = []
        child_series_ids = set()
        for row in all_rows:
            row_claims = series_claims(row)
            matching = [
                claim for claim in row_claims
                if int_or_none(claim.get("id")) == series_id
            ]
            if matching:
                direct.append(row)
                for claim in matching:
                    pos = claim.get("art_order")
                    if pos is None:
                        pos = claim.get("number")
                    if pos is not None:
                        positions.append(pos)
            else:
                expanded.append(row)
            for claim in row_claims:
                cid = int_or_none(claim.get("id"))
                if cid is not None and cid != series_id:
                    child_series_ids.add(cid)

        return {
            "author": case["author"],
            "work": case["work"],
            "art_id": int_or_none(art.get("id")),
            "art_title": art.get("title"),
            "art_authors": authors(art),
            "all_art_series_claims": [
                {
                    "id": int_or_none(c.get("id")),
                    "name": c.get("name") or c.get("title"),
                    "art_order": c.get("art_order"),
                    "number": c.get("number"),
                    "arts_count": c.get("arts_count"),
                    "unique_arts_count": c.get("unique_arts_count"),
                }
                for c in claims
            ],
            "selected_series": {
                "id": series_id,
                "name": selected.get("name") or selected.get("title"),
                "claim_art_order": selected.get("art_order"),
                "claim_number": selected.get("number"),
                "detail_parent_id": detail.get("parent_id"),
                "detail_arts_count": detail.get("arts_count"),
                "detail_unique_arts_count": detail.get("unique_arts_count"),
                "nested_series_count": len(dict_rows(detail.get("nested_series"))),
                "nested_series": [
                    {
                        "id": int_or_none(x.get("id")),
                        "name": x.get("name") or x.get("title"),
                        "arts_count": x.get("arts_count"),
                        "unique_arts_count": x.get("unique_arts_count"),
                    }
                    for x in dict_rows(detail.get("nested_series"))
                ][:20],
            },
            "composition": {
                "pages": page_summaries,
                "rows_observed": len(all_rows),
                "direct_rows": len(direct),
                "expanded_rows": len(expanded),
                "direct_ratio": round(len(direct) / len(all_rows), 4) if all_rows else 0.0,
                "positions_observed": positions[:100],
                "positions_count": len(positions),
                "other_series_ids_seen": sorted(child_series_ids)[:100],
            },
            "discovery": discovery,
        }

    async def run(self) -> dict[str, Any]:
        results = []
        async with LitResClient(
            delay_seconds=0.5,
            retry_backoff_seconds=1.0,
            timeout_seconds=30.0,
            user_agent="litres-parser-author-series-probe/0.1 (+github.com/ivzaislu/litres-pars)",
        ) as client:
            client._client.event_hooks["request"].append(self.recorder.on_request)
            client._client.event_hooks["response"].append(self.recorder.on_response)
            for case in CASES:
                results.append(await self.probe_series(client, case))

        return {
            "started_at": utcnow(),
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
        if int(comp["rows_observed"]) == 0:
            failures.append(f"{case['author']} / {case['work']}: no series rows")
        if int(comp["direct_rows"]) == 0:
            failures.append(f"{case['author']} / {case['work']}: no verified direct members")
    if int(report["requests"]["total_requests"]) > args.max_requests:
        failures.append("request budget exceeded")

    if failures:
        print("HARD FAILURES:", "; ".join(failures))
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="author-series-live-results")
    parser.add_argument("--max-requests", type=int, default=36)
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
