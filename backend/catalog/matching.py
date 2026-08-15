"""
Matching engine: turns a raw (title, author) read off a book spine into
a ranked list of catalog candidates with a confidence score.

This module has no Django or database dependency on purpose -- it takes
plain CatalogEntry objects in and returns plain MatchResult objects out,
so it can be unit tested with nothing but lists (see
catalog/tests/test_matching.py) and reused outside a request/response
cycle if needed.

catalog.csv was built specifically to make exact string matching fail
(see scripts/build_catalog.py's docstring):
  - duplicate editions of the same book under the same title/author
  - the same book under two different titles (US/UK)
  - two different books that happen to share a title
  - an omnibus alongside the individual volumes it contains
  - titles that are substrings of other titles
  - author names in initials / accented / transliterated / "Last, First"
    form

How this handles each case is explained inline below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz
from unidecode import unidecode

# Weight given to title vs. author similarity in the combined score.
# Title carries most of the signal, but author similarity is what lets
# us tell apart two different books that share an exact title (e.g. two
# catalog entries titled "The Alchemist") -- if we scored on title alone
# both would tie at a perfect score.
TITLE_WEIGHT = 0.7
AUTHOR_WEIGHT = 0.3

# When the VLM couldn't read an author at all, we don't want to punish
# the candidate as if the author were simply wrong. We use this as a
# neutral author score instead of 0.
NO_AUTHOR_READ_SCORE = 0.5

# How many alternates to keep for the review UI's "did you mean" list.
MAX_CANDIDATES = 3

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Casefold, transliterate accents, and strip punctuation.

    Deliberately does NOT reorder "Last, First" author names -- rapidfuzz's
    token_set_ratio (used below) compares the *set* of words, not their
    order, so "Rowling, J.K." and "J.K. Rowling" already score as a near
    match once punctuation is stripped to whitespace. Same mechanism
    handles "George R.R. Martin" vs "George R. R. Martin": once the dots
    become spaces, both normalize to the same token set.
    """
    if not text:
        return ""
    text = unidecode(text)
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


@dataclass
class CatalogEntry:
    catalog_id: str
    title: str
    author: str
    alt_titles: list[str] = field(default_factory=list)
    author_alt: list[str] = field(default_factory=list)
    year: int | None = None
    format: str = ""
    series: str = ""

    def title_candidates(self) -> list[str]:
        return [self.title, *self.alt_titles]

    def author_candidates(self) -> list[str]:
        return [self.author, *self.author_alt]


@dataclass
class Candidate:
    catalog_id: str
    title: str
    author: str
    score: float
    title_score: float
    author_score: float


@dataclass
class MatchResult:
    """Result of matching one (read_title, read_author) against a catalog."""

    read_title: str
    read_author: str
    candidates: list[Candidate]

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def tier(self, auto_accept: float, review_floor: float) -> str:
        """Classify into 'auto' / 'review' / 'unmatched'.

        'auto': confident enough to add to the library without asking.
        'review': a plausible candidate exists but isn't confident enough
                  to add silently -- surfaced to the user with
                  alternatives (see the Four Things We Check: "must not
                  be silently accepted, must not be silently dropped").
        'unmatched': nothing plausible in the catalog. Still goes to the
                  review step (as a blank/"add manually" card), never
                  silently dropped.
        """
        if not self.candidates:
            return "unmatched"
        top = self.candidates[0].score
        if top >= auto_accept:
            return "auto"
        if top >= review_floor:
            return "review"
        return "unmatched"


def _length_dampening(a: str, b: str) -> float:
    """Discount factor in [0.5, 1.0] based on how different a and b are in length.

    rapidfuzz's token_set_ratio scores a perfect 100 whenever one string's
    tokens are a subset of the other's -- which is exactly right for "a
    partial/garbled read should still match the full title" but exactly
    wrong for "Dune" vs "Dune Messiah": both are legitimate catalog
    entries, and without this, a read of "Dune Messiah" would tie
    "Dune" and "Dune Messiah" at the same score and (depending on sort
    stability) could pick the wrong one.

    We don't want to zero out subset matches entirely -- short, partial
    spine reads are common and should still win against unrelated
    titles. So this only *dampens* the score in proportion to the length
    gap, floor 0.5, rather than rejecting it outright.
    """
    if not a or not b:
        return 1.0
    shorter, longer = sorted((len(a), len(b)))
    ratio = shorter / longer
    return 0.5 + 0.5 * ratio


def _best_field_score(read_value: str, candidates: Iterable[str], neutral: float | None = None) -> float:
    read_norm = normalize(read_value)
    if not read_norm:
        return neutral if neutral is not None else 0.0
    best = 0.0
    for c in candidates:
        c_norm = normalize(c)
        if not c_norm:
            continue
        set_score = fuzz.token_set_ratio(read_norm, c_norm) / 100
        adjusted = set_score * _length_dampening(read_norm, c_norm)
        best = max(best, adjusted)
    return best


def score_entry(read_title: str, read_author: str, entry: CatalogEntry) -> Candidate:
    title_score = _best_field_score(read_title, entry.title_candidates())
    author_score = _best_field_score(
        read_author, entry.author_candidates(), neutral=NO_AUTHOR_READ_SCORE
    )
    combined = TITLE_WEIGHT * title_score + AUTHOR_WEIGHT * author_score
    return Candidate(
        catalog_id=entry.catalog_id,
        title=entry.title,
        author=entry.author,
        score=round(combined, 4),
        title_score=round(title_score, 4),
        author_score=round(author_score, 4),
    )


def match(
    read_title: str,
    read_author: str,
    catalog: Iterable[CatalogEntry],
    max_candidates: int = MAX_CANDIDATES,
) -> MatchResult:
    """Score read_title/read_author against every entry in `catalog`."""
    scored = [score_entry(read_title, read_author, entry) for entry in catalog]
    scored.sort(key=lambda c: c.score, reverse=True)
    return MatchResult(
        read_title=read_title,
        read_author=read_author,
        candidates=scored[:max_candidates],
    )
