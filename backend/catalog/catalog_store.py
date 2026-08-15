"""
Loads catalog entries either straight from catalog.csv (no DB needed --
used by tests and by the load_catalog management command) or from the
CatalogBook table (used at request time, with a small in-process cache
so we don't hit the DB on every single spine in every scan).
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from .matching import CatalogEntry

_cache: dict[str, tuple[float, list[CatalogEntry]]] = {}
_CACHE_TTL_SECONDS = 30


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split("|") if v.strip()]


def load_entries_from_csv(csv_path: str | Path) -> list[CatalogEntry]:
    entries = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            entries.append(
                CatalogEntry(
                    catalog_id=row["catalog_id"],
                    title=row["title"],
                    author=row["author"],
                    alt_titles=_split(row.get("alt_titles", "")),
                    author_alt=_split(row.get("author_alt", "")),
                    year=int(row["year"]) if row.get("year") else None,
                    format=row.get("format", ""),
                    series=row.get("series", ""),
                )
            )
    return entries


def get_catalog_entries(use_cache: bool = True) -> list[CatalogEntry]:
    """Catalog entries from the database, cached briefly in-process.

    The cache exists purely so a single scan (which may score a dozen
    detected spines against the whole catalog) doesn't re-query the DB
    per spine. It is *not* meant to survive a catalog edit for long --
    30s TTL, or call with use_cache=False right after `load_catalog`.
    """
    from .models import CatalogBook  # local import: keep this module DB-optional

    now = time.monotonic()
    if use_cache and "entries" in _cache:
        cached_at, entries = _cache["entries"]
        if now - cached_at < _CACHE_TTL_SECONDS:
            return entries

    entries = [
        CatalogEntry(
            catalog_id=b.catalog_id,
            title=b.title,
            author=b.author,
            alt_titles=b.alt_titles_list(),
            author_alt=b.author_alt_list(),
            year=b.year,
            format=b.format,
            series=b.series,
        )
        for b in CatalogBook.objects.all()
    ]
    _cache["entries"] = (now, entries)
    return entries


def clear_cache() -> None:
    _cache.pop("entries", None)
