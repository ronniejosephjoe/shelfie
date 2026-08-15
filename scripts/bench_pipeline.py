"""
Runs the full pipeline against every photo in photos/ and prints
measured local-model latency, per-image VLM read counts, and match
tier breakdown. This is what the README's latency table is built from
-- run it yourself after `manage.py load_catalog` to reproduce.

VLM_PROVIDER stays whatever's in your environment (mock by default).
With the mock provider this measures real local-model latency but only
an *estimated* VLM cost/latency (see README -- I do not have a funded
API key in the environment this was built in). Point VLM_PROVIDER=openai
with a real OPENAI_API_KEY to get measured hosted numbers instead; the
script prints whichever it actually got, unmodified.

Usage (from backend/):
    DJANGO_SETTINGS_MODULE=shelfie_backend.settings python ../scripts/bench_pipeline.py
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shelfie_backend.settings")

import django  # noqa: E402

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402

from catalog.catalog_store import get_catalog_entries  # noqa: E402
from scanner.models import ScanSession  # noqa: E402
from scanner.services.pipeline import run_pipeline  # noqa: E402
from scanner.services.vlm_client import get_vlm_client  # noqa: E402


def main():
    photos_dir = os.path.join(os.path.dirname(__file__), "..", "photos")
    photos = sorted(glob.glob(os.path.join(photos_dir, "*.jpg")))
    if not photos:
        print("No photos found in photos/. Run generate_test_photos.py first.")
        return

    catalog_size = len(get_catalog_entries(use_cache=False))
    vlm = get_vlm_client()
    print(f"catalog: {catalog_size} entries | vlm provider: {vlm.provider_name}\n")

    header = f"{'photo':<28}{'regions':>8}{'local_ms':>10}{'vlm_ms_total':>14}{'auto':>7}{'review':>8}{'unmatched':>11}{'errors':>8}"
    print(header)
    print("-" * len(header))

    totals = {"regions": 0, "local_ms": 0.0, "vlm_ms": 0.0, "auto": 0, "review": 0, "unmatched": 0, "errors": 0}

    for path in photos:
        with open(path, "rb") as f:
            upload = SimpleUploadedFile(os.path.basename(path), f.read(), content_type="image/jpeg")
        scan = ScanSession.objects.create(image=upload)
        t0 = time.perf_counter()
        run_pipeline(scan)
        wall_ms = (time.perf_counter() - t0) * 1000
        scan.refresh_from_db()

        books = list(scan.detected_books.all())
        auto = sum(1 for b in books if b.match_tier == "auto")
        review = sum(1 for b in books if b.match_tier == "review")
        unmatched = sum(1 for b in books if b.match_tier == "unmatched")
        errors = sum(1 for b in books if b.read_error)

        totals["regions"] += len(books)
        totals["local_ms"] += scan.local_model_ms or 0
        totals["vlm_ms"] += scan.vlm_total_ms or 0
        totals["auto"] += auto
        totals["review"] += review
        totals["unmatched"] += unmatched
        totals["errors"] += errors

        name = os.path.basename(path)
        print(
            f"{name:<28}{len(books):>8}{scan.local_model_ms or 0:>10.0f}"
            f"{scan.vlm_total_ms or 0:>14.0f}{auto:>7}{review:>8}{unmatched:>11}{errors:>8}"
            f"   (wall: {wall_ms:.0f}ms, status={scan.status})"
        )

    n = len(photos)
    print("-" * len(header))
    print(f"{'TOTAL / photo (avg)':<28}{totals['regions']/n:>8.1f}{totals['local_ms']/n:>10.0f}"
          f"{totals['vlm_ms']/n:>14.0f}{totals['auto']:>7}{totals['review']:>8}{totals['unmatched']:>11}{totals['errors']:>8}")
    if totals["regions"]:
        print(f"\nlocal model: {totals['local_ms']/totals['regions']:.1f} ms per detected region (Tesseract, 3 rotation passes)")


if __name__ == "__main__":
    main()
