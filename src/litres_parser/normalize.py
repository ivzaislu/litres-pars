from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_BB_TAG_RE = re.compile(r"\[/?[a-z][^\]]*\]", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


def clean_markup(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG_RE.sub(" ", text)
    text = _BB_TAG_RE.sub("", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return _SPACE_RE.sub(" ", text).strip()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean_markup(value)).casefold().replace("ё", "е")
    text = " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))
    return _SPACE_RE.sub(" ", text).strip()


def similarity(a: str, b: str) -> float:
    a_n, b_n = normalize_text(a), normalize_text(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    seq = SequenceMatcher(None, a_n, b_n).ratio()
    at, bt = set(a_n.split()), set(b_n.split())
    jacc = len(at & bt) / len(at | bt) if at and bt else 0.0
    return max(seq, jacc)


def _name_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_text(value).split()
        if len(token) > 1 and any(ch.isalpha() for ch in token)
    }


def person_similarity(a: str, b: str) -> float:
    at, bt = _name_tokens(a), _name_tokens(b)
    if at and bt and at == bt:
        return 1.0
    if at and bt:
        common = at & bt
        if len(common) >= 2 and (common == at or common == bt):
            return 1.0
        overlap = len(common) / max(len(at), len(bt))
    else:
        overlap = 0.0
    return max(overlap, similarity(a, b))


def person_identity_key(value: str) -> str:
    return " ".join(sorted(token for token in normalize_text(value).split() if token))
