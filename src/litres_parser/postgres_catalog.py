from __future__ import annotations

import json
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from .catalog import (
    _float_or_none,
    _int_or_none,
    authors_from_art,
    normalize_key,
    series_claims_from_art,
    utcnow_iso,
)


class PostgresCatalog:
    """PostgreSQL implementation of the LitRes provider cache contract."""

    def __init__(self, database_url: str) -> None:
        self.database_url = str(database_url)
        self.db = psycopg.connect(
            self.database_url,
            autocommit=True,
            row_factory=dict_row,
        )
        self.init_schema()

    def __enter__(self) -> "PostgresCatalog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def ping(self) -> bool:
        try:
            with self.db.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except psycopg.Error:
            return False

    def init_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS arts (
                art_id BIGINT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                title_norm TEXT NOT NULL DEFAULT '',
                art_type INTEGER,
                url TEXT NOT NULL DEFAULT '',
                authors_json TEXT NOT NULL DEFAULT '[]',
                series_json TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT NOT NULL,
                source_updated_at TEXT NOT NULL DEFAULT '',
                detail_cached SMALLINT NOT NULL DEFAULT 0,
                cached_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_arts_title_norm ON arts(title_norm)",
            "CREATE INDEX IF NOT EXISTS idx_arts_art_type ON arts(art_type)",
            """
            CREATE TABLE IF NOT EXISTS series (
                series_id BIGINT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                name_norm TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}',
                complete SMALLINT NOT NULL DEFAULT 0,
                cached_at TEXT NOT NULL,
                parent_id BIGINT,
                nested_series_json TEXT NOT NULL DEFAULT '[]',
                detail_cached SMALLINT NOT NULL DEFAULT 0
            )
            """,
            "ALTER TABLE series ADD COLUMN IF NOT EXISTS parent_id BIGINT",
            (
                "ALTER TABLE series ADD COLUMN IF NOT EXISTS "
                "nested_series_json TEXT NOT NULL DEFAULT '[]'"
            ),
            (
                "ALTER TABLE series ADD COLUMN IF NOT EXISTS "
                "detail_cached SMALLINT NOT NULL DEFAULT 0"
            ),
            "CREATE INDEX IF NOT EXISTS idx_series_name_norm ON series(name_norm)",
            """
            CREATE TABLE IF NOT EXISTS series_arts (
                series_id BIGINT NOT NULL,
                art_id BIGINT NOT NULL,
                position DOUBLE PRECISION,
                claim_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(series_id, art_id),
                FOREIGN KEY(series_id) REFERENCES series(series_id) ON DELETE CASCADE,
                FOREIGN KEY(art_id) REFERENCES arts(art_id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_series_arts_art ON series_arts(art_id)",
            (
                "CREATE INDEX IF NOT EXISTS idx_series_arts_order "
                "ON series_arts(series_id, position)"
            ),
            """
            CREATE TABLE IF NOT EXISTS series_expanded_arts (
                series_id BIGINT NOT NULL,
                art_id BIGINT NOT NULL,
                raw_context_json TEXT NOT NULL DEFAULT '{}',
                seen_at TEXT NOT NULL,
                PRIMARY KEY(series_id, art_id),
                FOREIGN KEY(series_id) REFERENCES series(series_id) ON DELETE CASCADE,
                FOREIGN KEY(art_id) REFERENCES arts(art_id) ON DELETE CASCADE
            )
            """,
            (
                "CREATE INDEX IF NOT EXISTS idx_series_expanded_art "
                "ON series_expanded_arts(art_id)"
            ),
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ]
        with self.db.transaction():
            with self.db.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)

    def _upsert_art(self, row: dict[str, Any], *, detail: bool) -> bool:
        art_id = _int_or_none(row.get("id"))
        if art_id is None:
            return False

        title = str(row.get("title") or "").strip()
        claims = series_claims_from_art(row)
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT detail_cached FROM arts WHERE art_id = %s",
                (art_id,),
            )
            existing = cur.fetchone()

        detail_cached = int(
            bool(detail) or bool(existing and existing["detail_cached"])
        )
        incoming_is_detail = int(bool(detail))
        now = utcnow_iso()

        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO arts(
                    art_id, title, title_norm, art_type, url, authors_json, series_json,
                    raw_json, source_updated_at, detail_cached, cached_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT(art_id) DO UPDATE SET
                    title=CASE
                        WHEN excluded.title != '' THEN excluded.title ELSE arts.title END,
                    title_norm=CASE
                        WHEN excluded.title_norm != ''
                        THEN excluded.title_norm ELSE arts.title_norm END,
                    art_type=COALESCE(excluded.art_type, arts.art_type),
                    url=CASE
                        WHEN excluded.url != '' THEN excluded.url ELSE arts.url END,
                    authors_json=CASE
                        WHEN %s = 1
                             OR arts.detail_cached = 0
                             OR excluded.authors_json != '[]'
                        THEN excluded.authors_json ELSE arts.authors_json END,
                    series_json=CASE
                        WHEN %s = 1
                             OR arts.detail_cached = 0
                             OR excluded.series_json != '[]'
                        THEN excluded.series_json ELSE arts.series_json END,
                    raw_json=CASE
                        WHEN %s = 1 OR arts.detail_cached = 0
                        THEN excluded.raw_json ELSE arts.raw_json END,
                    source_updated_at=CASE
                        WHEN %s = 1 OR arts.detail_cached = 0
                        THEN excluded.source_updated_at ELSE arts.source_updated_at END,
                    detail_cached=GREATEST(
                        arts.detail_cached, excluded.detail_cached
                    ),
                    cached_at=excluded.cached_at
                """,
                (
                    art_id,
                    title,
                    normalize_key(title),
                    _int_or_none(row.get("art_type")),
                    str(row.get("url") or ""),
                    json.dumps(
                        authors_from_art(row),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        claims,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
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
                cur.execute(
                    "DELETE FROM series_arts WHERE art_id = %s",
                    (art_id,),
                )

        for claim in claims:
            series_id = int(claim["id"])
            name = str(claim.get("name") or claim.get("title") or "").strip()
            self._upsert_series(
                {"id": series_id, "name": name},
                complete=None,
                detail=False,
            )
            with self.db.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO series_arts(
                        series_id, art_id, position, claim_json
                    ) VALUES (%s, %s, %s, %s)
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
                        json.dumps(
                            claim,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
        return True

    def upsert_art(
        self,
        row: dict[str, Any],
        *,
        detail: bool = False,
        commit: bool = True,
    ) -> bool:
        if not commit:
            return self._upsert_art(row, detail=detail)
        with self.db.transaction():
            return self._upsert_art(row, detail=detail)

    def upsert_arts(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        detail: bool = False,
    ) -> int:
        stored = 0
        with self.db.transaction():
            for row in rows:
                if isinstance(row, dict) and self._upsert_art(row, detail=detail):
                    stored += 1
        return stored

    def _upsert_series(
        self,
        row: dict[str, Any],
        *,
        complete: bool | None,
        detail: bool,
    ) -> bool:
        series_id = _int_or_none(row.get("id"))
        if series_id is None:
            return False

        name = str(row.get("name") or row.get("title") or "").strip()
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT complete, raw_json, name, parent_id,
                       nested_series_json, detail_cached
                FROM series
                WHERE series_id = %s
                """,
                (series_id,),
            )
            existing = cur.fetchone()

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

        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO series(
                    series_id, name, name_norm, raw_json, complete, cached_at,
                    parent_id, nested_series_json, detail_cached
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT(series_id) DO UPDATE SET
                    name=CASE
                        WHEN excluded.name != ''
                        THEN excluded.name ELSE series.name END,
                    name_norm=CASE
                        WHEN excluded.name_norm != ''
                        THEN excluded.name_norm ELSE series.name_norm END,
                    raw_json=CASE
                        WHEN excluded.detail_cached = 1
                        THEN excluded.raw_json ELSE series.raw_json END,
                    complete=excluded.complete,
                    cached_at=excluded.cached_at,
                    parent_id=CASE
                        WHEN excluded.detail_cached = 1
                        THEN excluded.parent_id ELSE series.parent_id END,
                    nested_series_json=CASE
                        WHEN excluded.detail_cached = 1
                        THEN excluded.nested_series_json
                        ELSE series.nested_series_json END,
                    detail_cached=GREATEST(
                        series.detail_cached, excluded.detail_cached
                    )
                """,
                (
                    series_id,
                    final_name,
                    normalize_key(final_name),
                    json.dumps(
                        raw,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    final_complete,
                    utcnow_iso(),
                    parent_id,
                    json.dumps(
                        nested,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    int(incoming_detail or existing_detail),
                ),
            )
        return True

    def upsert_series(
        self,
        row: dict[str, Any],
        *,
        complete: bool | None = None,
        detail: bool = False,
        commit: bool = True,
    ) -> bool:
        if not commit:
            return self._upsert_series(
                row,
                complete=complete,
                detail=detail,
            )
        with self.db.transaction():
            return self._upsert_series(
                row,
                complete=complete,
                detail=detail,
            )

    def replace_series_arts(
        self,
        series_id: int,
        rows: Iterable[dict[str, Any]],
        *,
        series_detail: dict[str, Any] | None = None,
    ) -> int:
        series_id = int(series_id)
        materialized = [row for row in rows if isinstance(row, dict)]
        now = utcnow_iso()

        with self.db.transaction():
            self._upsert_series(
                series_detail or {"id": series_id},
                complete=False,
                detail=series_detail is not None,
            )
            with self.db.cursor() as cur:
                cur.execute(
                    "DELETE FROM series_arts WHERE series_id = %s",
                    (series_id,),
                )
                cur.execute(
                    "DELETE FROM series_expanded_arts WHERE series_id = %s",
                    (series_id,),
                )

            for row in materialized:
                self._upsert_art(row, detail=False)
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

                with self.db.cursor() as cur:
                    if claim is None:
                        cur.execute(
                            """
                            INSERT INTO series_expanded_arts(
                                series_id, art_id, raw_context_json, seen_at
                            ) VALUES (%s, %s, %s, %s)
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
                    else:
                        cur.execute(
                            """
                            INSERT INTO series_arts(
                                series_id, art_id, position, claim_json
                            ) VALUES (%s, %s, %s, %s)
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
                                json.dumps(
                                    claim,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            ),
                        )

            with self.db.cursor() as cur:
                cur.execute(
                    """
                    UPDATE series
                    SET complete = 1, cached_at = %s
                    WHERE series_id = %s
                    """,
                    (now, series_id),
                )

        return len(materialized)

    @staticmethod
    def _decode_art(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["authors"] = json.loads(result.pop("authors_json") or "[]")
        result["series"] = json.loads(result.pop("series_json") or "[]")
        result["raw"] = json.loads(result.pop("raw_json") or "{}")
        if "claim_json" in result:
            result["claim"] = json.loads(result.pop("claim_json") or "{}")
        if "raw_context_json" in result:
            result["context"] = json.loads(
                result.pop("raw_context_json") or "{}"
            )
        result["detail_cached"] = bool(result["detail_cached"])
        return result

    @staticmethod
    def _decode_series(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["raw"] = json.loads(result.pop("raw_json") or "{}")
        result["nested_series"] = json.loads(
            result.pop("nested_series_json", "[]") or "[]"
        )
        result["complete"] = bool(result["complete"])
        result["detail_cached"] = bool(result.get("detail_cached"))
        return result

    def get_art(self, art_id: int) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM arts WHERE art_id = %s",
                (int(art_id),),
            )
            row = cur.fetchone()
        return self._decode_art(row) if row is not None else None

    def find_arts(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        key = normalize_key(query)
        if not key:
            return []
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT art_id FROM arts
                WHERE title_norm = %s OR title_norm LIKE %s
                ORDER BY CASE WHEN title_norm = %s THEN 0 ELSE 1 END,
                         title_norm, art_id
                LIMIT %s
                """,
                (key, f"%{key}%", key, max(1, int(limit))),
            )
            rows = cur.fetchall()
        return [
            item
            for row in rows
            if (item := self.get_art(int(row["art_id"]))) is not None
        ]

    def find_series(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        key = normalize_key(query)
        if not key:
            return []
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM series
                WHERE name_norm = %s OR name_norm LIKE %s
                ORDER BY CASE WHEN name_norm = %s THEN 0 ELSE 1 END,
                         name_norm, series_id
                LIMIT %s
                """,
                (key, f"%{key}%", key, max(1, int(limit))),
            )
            rows = cur.fetchall()
        return [self._decode_series(row) for row in rows]

    def get_series(self, series_id: int) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM series WHERE series_id = %s",
                (int(series_id),),
            )
            row = cur.fetchone()
        return self._decode_series(row) if row is not None else None

    def series_for_art(self, art_id: int) -> list[dict[str, Any]]:
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT s.*, sa.position, sa.claim_json
                FROM series_arts sa
                JOIN series s ON s.series_id = sa.series_id
                WHERE sa.art_id = %s
                ORDER BY sa.position IS NULL, sa.position, s.series_id
                """,
                (int(art_id),),
            )
            rows = cur.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._decode_series(row)
            item["claim"] = json.loads(item.pop("claim_json") or "{}")
            result.append(item)
        return result

    def series_arts(self, series_id: int) -> list[dict[str, Any]]:
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT a.*, sa.position, sa.claim_json
                FROM series_arts sa
                JOIN arts a ON a.art_id = sa.art_id
                WHERE sa.series_id = %s
                ORDER BY sa.position IS NULL, sa.position, a.art_id
                """,
                (int(series_id),),
            )
            rows = cur.fetchall()
        return [self._decode_art(row) for row in rows]

    def series_expanded_arts(
        self,
        series_id: int,
    ) -> list[dict[str, Any]]:
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT a.*, sea.raw_context_json, sea.seen_at
                FROM series_expanded_arts sea
                JOIN arts a ON a.art_id = sea.art_id
                WHERE sea.series_id = %s
                ORDER BY a.title_norm, a.art_id
                """,
                (int(series_id),),
            )
            rows = cur.fetchall()
        return [self._decode_art(row) for row in rows]

    def set_state(self, key: str, value: dict[str, Any]) -> None:
        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sync_state(key, value_json, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    utcnow_iso(),
                ),
            )

    def get_state(self, key: str) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT value_json FROM sync_state WHERE key = %s",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def stats(self) -> dict[str, int]:
        with self.db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM arts")
            arts = int(cur.fetchone()["count"])
            cur.execute("SELECT COUNT(*) AS count FROM series")
            series = int(cur.fetchone()["count"])
            cur.execute("SELECT COUNT(*) AS count FROM series_arts")
            memberships = int(cur.fetchone()["count"])
            cur.execute("SELECT COUNT(*) AS count FROM series_expanded_arts")
            expanded = int(cur.fetchone()["count"])
            cur.execute(
                "SELECT COUNT(*) AS count FROM series WHERE complete = 1"
            )
            complete_series = int(cur.fetchone()["count"])
        return {
            "arts": arts,
            "series": series,
            "series_memberships": memberships,
            "series_expanded_rows": expanded,
            "complete_series": complete_series,
        }
