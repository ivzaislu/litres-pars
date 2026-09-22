from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from .normalize import clean_markup, normalize_text, person_identity_key, person_similarity, similarity

TEXT_ART_TYPE = 0
AUDIO_ART_TYPE = 1


def intish(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def instance(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    nested = row.get("instance")
    return nested if isinstance(nested, dict) else row


def normalize_litres_url(value: Any, *, base_url: str = "https://www.litres.ru") -> str:
    text = clean_markup(value)
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith(("http://", "https://")):
        return text
    return urljoin(base_url.rstrip("/") + "/", text.lstrip("/"))


def authors_from_art(row: dict[str, Any]) -> list[str]:
    people = row.get("persons")
    if not isinstance(people, list):
        return []
    result: list[str] = []
    for person in people:
        if not isinstance(person, dict) or person.get("role") != "author":
            continue
        name = clean_markup(person.get("full_name"))
        if name and name not in result:
            result.append(name)
    return result


def identity_authors(row: dict[str, Any]) -> tuple[str, ...]:
    values = {person_identity_key(value) for value in authors_from_art(row)}
    values.discard("")
    return tuple(sorted(values))


def same_litres_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_title = normalize_text(clean_markup(left.get("title")))
    right_title = normalize_text(clean_markup(right.get("title")))
    if not left_title or left_title != right_title:
        return False
    left_authors = identity_authors(left)
    right_authors = identity_authors(right)
    return bool(left_authors) and left_authors == right_authors


def canonical_art_id(row: dict[str, Any]) -> int | None:
    """Return only a conservative text-version candidate.

    LitRes alternative_versions is broader than strict work identity. LINKED is
    never enough. Only an explicit SYNCED text alternative is returned; callers
    should still fetch both details and verify title+author identity.
    """
    art_id = intish(row.get("id"))
    if not art_id:
        return None
    if intish(row.get("art_type")) == TEXT_ART_TYPE:
        return art_id
    alternatives = row.get("alternative_versions")
    if isinstance(alternatives, list):
        for item in alternatives:
            if not isinstance(item, dict):
                continue
            if (
                clean_markup(item.get("link_type")).upper() == "SYNCED"
                and intish(item.get("art_type")) == TEXT_ART_TYPE
            ):
                candidate = intish(item.get("id"))
                if candidate:
                    return candidate
    return art_id


def series_claim(row: dict[str, Any], series_id: int) -> dict[str, Any] | None:
    claims = row.get("series")
    if not isinstance(claims, list):
        return None
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        if intish(claim.get("id")) == int(series_id) and claim.get("art_order") is not None:
            return claim
    return None


def candidate_score(source_title: str, source_authors: list[str], candidate_title: str, candidate_authors: list[str]) -> tuple[float, float, float]:
    title_score = similarity(source_title, candidate_title)
    if source_authors:
        if candidate_authors:
            best = [max((person_similarity(a, b) for b in candidate_authors), default=0.0) for a in source_authors]
            author_score = sum(best) / len(best)
        else:
            author_score = 0.0
        score = title_score * 0.78 + author_score * 0.22
        if author_score < 0.45:
            score *= 0.72
    else:
        author_score = 0.5
        score = title_score
    return score, title_score, author_score
