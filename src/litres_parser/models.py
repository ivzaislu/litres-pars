from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LitResCandidate:
    art_id: int
    title: str
    authors: list[str]
    art_type: int | None
    url: str
    raw: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("raw", None)
        return result


@dataclass(slots=True)
class LitResMatch:
    status: str
    candidate: LitResCandidate | None
    score: float
    title_score: float
    author_score: float
    margin: float
    method: str
    query: str
    reason: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidate": self.candidate.public_dict() if self.candidate else None,
            "score": round(self.score, 4),
            "title_score": round(self.title_score, 4),
            "author_score": round(self.author_score, 4),
            "margin": round(self.margin, 4),
            "method": self.method,
            "query": self.query,
            "reason": self.reason,
        }


@dataclass(slots=True)
class LitResFacetsPage:
    rows: list[dict[str, Any]]
    next_offset: int | None
    total: int | None
