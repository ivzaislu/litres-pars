from .client import (
    DEFAULT_API_BASE_URL,
    DEFAULT_WEB_BASE_URL,
    MAX_FACETS_PAGE_SIZE,
    MAX_SERIES_PAGE_SIZE,
    LitResClient,
)
from .identity import (
    AUDIO_ART_TYPE,
    TEXT_ART_TYPE,
    authors_from_art,
    canonical_art_id,
    normalize_litres_url,
    same_litres_identity,
    series_claim,
)
from .models import LitResCandidate, LitResFacetsPage, LitResMatch
from .normalize import clean_markup, normalize_text, person_similarity, similarity

__all__ = [
    "AUDIO_ART_TYPE",
    "TEXT_ART_TYPE",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_WEB_BASE_URL",
    "MAX_FACETS_PAGE_SIZE",
    "MAX_SERIES_PAGE_SIZE",
    "LitResCandidate",
    "LitResClient",
    "LitResFacetsPage",
    "LitResMatch",
    "authors_from_art",
    "canonical_art_id",
    "clean_markup",
    "normalize_litres_url",
    "normalize_text",
    "person_similarity",
    "same_litres_identity",
    "series_claim",
    "similarity",
]
