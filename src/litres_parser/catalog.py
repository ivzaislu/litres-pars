from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SPACE_RE = re.compile(r"\s+")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    text = " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))
    return _SPACE_RE.sub(" ", text).strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def authors_from_art(row: dict[str, Any]) -> list[str]:
    persons = row.get("persons")
    if not isinstance(persons, list):
        return []
    result: list[str] = []
    for person in persons:
        if not isinstance(person, dict) or person.get("role") != "author":
            continue
        name = str(person.get("full_name") or "").strip()
        if name and name not in result:
            result.append(name)
    return result


def series_claims_from_art(row: dict[str, Any]) -> list[dict[str, Any]]:
    claims = row.get("series")
    if not isinstance(claims, list):
        return []
    result: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        if _int_or_none(claim.get("id")) is None:
            continue
        result.append(claim)
    return result


class LitResCatalog:
    """Local SQLite cache for raw LitRes provider data.

    The catalog keeps provider identity intact:
    - every LitRes art_id remains a separate art;
    - direct series membership is stored separately from expanded hierarchy rows;
    - no canonical-work or application-specific identity decisions live here.
    """

    def __init__(self, path: str | Path = "litres.sqlite3") -> None:
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self.init_schema()

    def __enter__(self) -> "LitResCatalog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def _column_names(self, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in self.db.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        if name not in self._column_names(table):
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def init_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS arts (
                art_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                title_norm TEXT NOT NULL DEFAULT '',
                art_type INTEGER,
                url TEXT NOT NULL DEFAULT '',
                authors_json TEXT NOT NULL DEFAULT '[]',
                series_json TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT NOT NULL,
                source_updated_at TEXT NOT NULL DEFAULT '',
                detail_cached INTEGER NOT NULL DEFAULT 0,
                cached_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_arts_title_norm ON arts(title_norm);
            CREATE INDEX IF NOT EXISTS idx_arts_art_type ON arts(art_type);

            CREATE TABLE IF NOT EXISTS series (
                series_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                name_norm TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}',
                complete INTEGER NOT NULL DEFAULT 0,
                cached_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_series_name_norm ON series(name_norm);

            CREATE TABLE IF NOT EXISTS series_arts (
                series_id INTEGER NOT NULL,
                art_id INTEGER NOT NULL,
                position REAL,
                claim_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(series_id, art_id),
                FOREIGN KEY(series_id) REFERENCES series(series_id) ON DELETE CASCADE,
                FOREIGN KEY(art_id) REFERENCES arts(art_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_series_arts_art ON series_arts(art_id);
            CREATE INDEX IF NOT EXISTS idx_series_arts_order ON series_arts(series_id, position);

            CREATE TABLE IF NOT EXISTS series_expanded_arts (
                series_id INTEGER NOT NULL,
                art_id INTEGER NOT NULL,
                raw_context_json TEXT NOT NULL DEFAULT '{}',
                seen_at TEXT NOT NULL,
                PRIMARY KEY(series_id, art_id),
                FOREIGN KEY(series_id) REFERENCES series(series_id) ON DELETE CASCADE,
                FOREIGN KEY(art_id) REFERENCES arts(art_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_series_expanded_art
                ON series_expanded_arts(art_id);

            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Idempotent migration path from the earlier cache schema.
        self._ensure_column("series", "parent_id", "INTEGER")
        self._ensure_column("series", "nested_series_json", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("series", "detail_cached", "INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    def upsert_art(
        self,
        row: dict[str, Any],
        *,
        detail: bool = False,
        commit: bool = True,
    ) -> bool:
        art_id = _int_or_none(row.get("id"))
        if art_id is None:
            return False

        title = str(row.get("title") or "").strip()
        claims = series_claims_from_art(row)
        existing = self.db.execute(
            "SELECT detail_cached FROM arts WHERE art_id = ?", (art_id,)
        ).fetchone()
        detail_cached = int(bool(detail) or bool(existing and existing["detail_cached"]))
        incoming_is_detail = int(bool(detail))
        now = utcnow_iso()

        self.db.execute(
            """
            INSERT INTO arts(
                art_id, title, title_norm, art_type, url, authors_json, series_json,
                raw_json, source_updated_at, detail_cached, cached_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(art_id) DO UPDATE SET
                title=CASE WHEN excluded.title != '' THEN excluded.title ELSE arts.title END,
                title_norm=CASE
                    WHEN excluded.title_norm != '' THEN excluded.title_norm ELSE arts.title_norm END,
                art_type=COALESCE(excluded.art_type, arts.art_type),
                url=CASE WHEN excluded.url != '' THEN excluded.url ELSE arts.url END,
                authors_json=CASE
                    WHEN ? = 1 OR arts.detail_cached = 0 OR excluded.authors_json != '[]'
                    THEN excluded.authors_json ELSE arts.authors_json END,
                series_json=CASE
                    WHEN ? = 1 OR arts.detail_cached = 0 OR excluded.series_json != '[]'
                    THEN excluded.series_json ELSE arts.series_json END,
                raw_json=CASE
                    WHEN ? = 1 OR arts.detail_cached = 0
                    THEN excluded.raw_json ELSE arts.raw_json END,
                source_updated_at=CASE
                    WHEN ? = 1 OR arts.detail_cached = 0
                    THEN excluded.source_updated_at ELSE arts.source_updated_at END,
                detail_cached=MAX(arts.detail_cached, excluded.detail_cached),
                cached_at=excluded.cached_at
            """,
            (
                art_id,
                title,
                normalize_key(title),
                _int_or_none(row.get("art_type")),
                str(row.get("url") or ""),
                json.dumps(authors_from_art(row), ensure_ascii=False, separators=(",", ":")),
                json.dumps(claims, ensure_ascii=False, separators=(",", ":")),
                json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                str(row.get("last_updated_at") or ""),
                detail_cached,
                now,
                incoming_is_detail,
                incoming_is_detail,
                incoming_is_detail,
                incoming_is_detail,
            ),
        )

        if detail:
            self.db.execute("DELETE FROM series_arts WHERE art_id = ?", (art_id,))

        for claim in claims:
            series_id = int(claim["id"])
            name = str(claim.get("name") or claim.get("title") or "").strip()
            self.upsert_series(
                {"id": series_id, "name": name},
                complete=None,
                detail=False,
                commit=False,
            )
            self.db.execute(
                """
                INSERT INTO series_arts(series_id, art_id, position, claim_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(series_id, art_id) DO UPDATE SET
                    position=excluded.position,
                    claim_json=excluded.claim_json
                """,
                (
                    series_id,
                    art_id,
                    _float_or_none(
                        claim.get("art_order")
                        if claim.get("art_order") is not None
                        else claim.get("number")
                    ),
                    json.dumps(claim, ensure_ascii=False, separators=(",", ":")),
                ),
            )

        if commit:
            self.db.commit()
        return True

    def upsert_arts(self, rows: Iterable[dict[str, Any]], *, detail: bool = False) -> int:
        stored = 0
        with self.db:
            for row in rows:
                if isinstance(row, dict) and self.upsert_art(
                    row, detail=detail, commit=False
                ):
                    stored += 1
        return stored

    def upsert_series(
        self,
        row: dict[str, Any],
        *,
        complete: bool | None = None,
        detail: bool = False,
        commit: bool = True,
    ) -> bool:
        series_id = _int_or_none(row.get("id"))
        if series_id is None:
            return False

        name = str(row.get("name") or row.get("title") or "").strip()
        existing = self.db.execute(
            """
            SELECT complete, raw_json, name, parent_id, nested_series_json, detail_cached
            FROM series WHERE series_id = ?
            """,
            (series_id,),
        ).fetchone()

        final_complete = (
            int(bool(complete))
            if complete is not None
            else int(existing["complete"] if existing else 0)
        )
        existing_detail = bool(existing and existing["detail_cached"])
        incoming_detail = bool(detail)
        raw = (
            row
            if incoming_detail or not existing
            else json.loads(existing["raw_json"] or "{}")
        )
        final_name = name or (str(existing["name"]) if existing else "")
        parent_id = (
            _int_or_none(row.get("parent_id"))
            if incoming_detail
            else (existing["parent_id"] if existing else None)
        )
        nested = (
            row.get("nested_series")
            if incoming_detail and isinstance(row.get("nested_series"), list)
            else (
                json.loads(existing["nested_series_json"] or "[]")
                if existing
                else []
            )
        )

        self.db.execute(
            """
            INSERT INTO series(
                series_id, name, name_norm, raw_json, complete, cached_at,
                parent_id, nested_series_json, detail_cached
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id) DO UPDATE SET
                name=CASE WHEN excluded.name != '' THEN excluded.name ELSE series.name END,
                name_norm=CASE
                    WHEN excluded.name_norm != '' THEN excluded.name_norm ELSE series.name_norm END,
                raw_json=CASE
                    WHEN excluded.detail_cached = 1 THEN excluded.raw_json ELSE series.raw_json END,
                complete=excluded.complete,
                cached_at=excluded.cached_at,
                parent_id=CASE
                    WHEN excluded.detail_cached = 1 THEN excluded.parent_id ELSE series.parent_id END,
                nested_series_json=CASE
                    WHEN excluded.detail_cached = 1
                    THEN excluded.nested_series_json ELSE series.nested_series_json END,
                detail_cached=MAX(series.detail_cached, excluded.detail_cached)
            """,
            (
                series_id,
                final_name,
                normalize_key(final_name),
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                final_complete,
                utcnow_iso(),
                parent_id,
                json.dumps(nested, ensure_ascii=False, separators=(",", ":")),
                int(incoming_detail or existing_detail),
            ),
        )

        if commit:
            self.db.commit()
        return True

    def replace_series_arts(
        self,
        series_id: int,
        rows: Iterable[dict[str, Any]],
        *,
        series_detail: dict[str, Any] | None = None,
    ) -> int:
        """Replace one series composition with verified direct membership.

        LitRes may return descendants/expanded hierarchy rows from
        /series/{id}/arts. Only rows whose own series[] contains the requested
        series_id are inserted into series_arts. Everything else is retained in
        series_expanded_arts for observability without corrupting membership.
        """
        series_id = int(series_id)
        materialized = [row for row in rows if isinstance(row, dict)]
        now = utcnow_iso()

        with self.db:
            self.upsert_series(
                series_detail or {"id": series_id},
                complete=False,
                detail=series_detail is not None,
                commit=False,
            )
            self.db.execute("DELETE FROM series_arts WHERE series_id = ?", (series_id,))
            self.db.execute(
                "DELETE FROM series_expanded_arts WHERE series_id = ?", (series_id,)
            )

            for row in materialized:
                self.upsert_art(row, detail=False, commit=False)
                art_id = _int_or_none(row.get("id"))
                if art_id is None:
                    continue

                claim = next(
                    (
                        item
                        for item in series_claims_from_art(row)
                        if _int_or_none(item.get("id")) == series_id
                    ),
                    None,
                )
                if claim is None:
                    self.db.execute(
                        """
                        INSERT INTO series_expanded_arts(
                            series_id, art_id, raw_context_json, seen_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(series_id, art_id) DO UPDATE SET
                            raw_context_json=excluded.raw_context_json,
                            seen_at=excluded.seen_at
                        """,
                        (
                            series_id,
                            art_id,
                            json.dumps(
                                {"series": row.get("series") or []},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            now,
                        ),
                    )
                    continue

                self.db.execute(
                    """
                    INSERT INTO series_arts(series_id, art_id, position, claim_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(series_id, art_id) DO UPDATE SET
                        position=excluded.position,
                        claim_json=excluded.claim_json
                    """,
                    (
                        series_id,
                        art_id,
                        _float_or_none(
                            claim.get("art_order")
                            if claim.get("art_order") is not None
                            else claim.get("number")
                        ),
                        json.dumps(claim, ensure_ascii=False, separators=(",", ":")),
                    ),
                )

            self.db.execute(
                "UPDATE series SET complete = 1, cached_at = ? WHERE series_id = ?",
                (now, series_id),
            )

        return len(materialized)

    def get_art(self, art_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM arts WHERE art_id = ?", (int(art_id),)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["authors"] = json.loads(result.pop("authors_json") or "[]")
        result["series"] = json.loads(result.pop("series_json") or "[]")
        result["raw"] = json.loads(result.pop("raw_json") or "{}")
        result["detail_cached"] = bool(result["detail_cached"])
        return result

    def find_arts(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        key = normalize_key(query)
        if not key:
            return []
        rows = self.db.execute(
            """
            SELECT art_id FROM arts
            WHERE title_norm = ? OR title_norm LIKE ?
            ORDER BY CASE WHEN title_norm = ? THEN 0 ELSE 1 END, title_norm, art_id
            LIMIT ?
            """,
            (key, f"%{key}%", key, max(1, int(limit))),
        ).fetchall()
        return [
            item
            for row in rows
            if (item := self.get_art(int(row["art_id"]))) is not None
        ]

    def find_series(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        key = normalize_key(query)
        if not key:
            return []
        rows = self.db.execute(
            """
            SELECT * FROM series
            WHERE name_norm = ? OR name_norm LIKE ?
            ORDER BY CASE WHEN name_norm = ? THEN 0 ELSE 1 END, name_norm, series_id
            LIMIT ?
            """,
            (key, f"%{key}%", key, max(1, int(limit))),
        ).fetchall()
        return [self._series_row(row) for row in rows]

    def _series_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["raw"] = json.loads(result.pop("raw_json") or "{}")
        result["nested_series"] = json.loads(
            result.pop("nested_series_json", "[]") or "[]"
        )
        result["complete"] = bool(result["complete"])
        result["detail_cached"] = bool(result.get("detail_cached"))
        return result

    def get_series(self, series_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM series WHERE series_id = ?", (int(series_id),)
        ).fetchone()
        return self._series_row(row) if row is not None else None

    def series_for_art(self, art_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT s.*, sa.position, sa.claim_json
            FROM series_arts sa
            JOIN series s ON s.series_id = sa.series_id
            WHERE sa.art_id = ?
            ORDER BY sa.position IS NULL, sa.position, s.series_id
            """,
            (int(art_id),),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["raw"] = json.loads(item.pop("raw_json") or "{}")
            item["nested_series"] = json.loads(
                item.pop("nested_series_json", "[]") or "[]"
            )
            item["claim"] = json.loads(item.pop("claim_json") or "{}")
            item["complete"] = bool(item["complete"])
            item["detail_cached"] = bool(item.get("detail_cached"))
            result.append(item)
        return result

    def _art_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["authors"] = json.loads(item.pop("authors_json") or "[]")
        item["series"] = json.loads(item.pop("series_json") or "[]")
        item["raw"] = json.loads(item.pop("raw_json") or "{}")
        if "claim_json" in item:
            item["claim"] = json.loads(item.pop("claim_json") or "{}")
        if "raw_context_json" in item:
            item["context"] = json.loads(item.pop("raw_context_json") or "{}")
        item["detail_cached"] = bool(item["detail_cached"])
        return item

    def series_arts(self, series_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT a.*, sa.position, sa.claim_json
            FROM series_arts sa
            JOIN arts a ON a.art_id = sa.art_id
            WHERE sa.series_id = ?
            ORDER BY sa.position IS NULL, sa.position, a.art_id
            """,
            (int(series_id),),
        ).fetchall()
        return [self._art_row(row) for row in rows]

    def series_expanded_arts(self, series_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT a.*, sea.raw_context_json, sea.seen_at
            FROM series_expanded_arts sea
            JOIN arts a ON a.art_id = sea.art_id
            WHERE sea.series_id = ?
            ORDER BY a.title_norm, a.art_id
            """,
            (int(series_id),),
        ).fetchall()
        return [self._art_row(row) for row in rows]

    def set_state(self, key: str, value: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO sync_state(key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=excluded.updated_at
            """,
            (
                key,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                utcnow_iso(),
            ),
        )
        self.db.commit()

    def get_state(self, key: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT value_json FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def stats(self) -> dict[str, int]:
        arts = int(self.db.execute("SELECT COUNT(*) FROM arts").fetchone()[0])
        series = int(self.db.execute("SELECT COUNT(*) FROM series").fetchone()[0])
        memberships = int(
            self.db.execute("SELECT COUNT(*) FROM series_arts").fetchone()[0]
        )
        expanded = int(
            self.db.execute("SELECT COUNT(*) FROM series_expanded_arts").fetchone()[0]
        )
        complete_series = int(
            self.db.execute("SELECT COUNT(*) FROM series WHERE complete = 1").fetchone()[0]
        )
        return {
            "arts": arts,
            "series": series,
            "series_memberships": memberships,
            "series_expanded_rows": expanded,
            "complete_series": complete_series,
        }
