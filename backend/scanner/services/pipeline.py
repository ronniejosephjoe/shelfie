"""
Orchestrates one scan end to end: detect spines (local) -> read each
spine (hosted VLM) -> match each read against the catalog -> persist
DetectedBook rows -> auto-add the high-confidence ones to the library.

Runs synchronously inside the request. For an 8-hour scope with a
single demo user that's a deliberate simplification, not an oversight
-- see README "what we cut" for the tradeoff (a real product would move
this to a task queue so the upload request returns immediately and the
client polls/subscribes for progress).

The one rule this module is built around: nothing in here raises past
run_pipeline(). Every failure mode in the brief's "Four Things" list
(model timeout, malformed JSON, zero detections, unreadable spine) is
turned into pipeline state (a ScanSession status, or a DetectedBook
with an error/tier set) rather than an exception, so the view layer
never has to guess whether a 500 means "your photo was fine and we
broke" or "something about your photo broke us."
"""
from __future__ import annotations

import logging
import time

import cv2
import numpy as np
from django.conf import settings
from PIL import Image

from catalog.catalog_store import get_catalog_entries
from catalog.matching import match as match_catalog

from ..models import DetectedBook, LibraryBook, ScanSession
from .spine_detector import TesseractSpineDetector
from .vlm_client import get_vlm_client

logger = logging.getLogger("shelfie")

# Padding added around a detected text region before cropping for the
# VLM read -- spine art/edges just outside the text often carry useful
# context (author name in a different font size, etc).
CROP_PADDING_FRACTION = 0.08


def _load_bgr(scan_session: ScanSession) -> np.ndarray | None:
    try:
        scan_session.image.open()
        data = scan_session.image.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:
        logger.exception("failed to decode uploaded image for scan %s", scan_session.id)
        return None
    finally:
        try:
            scan_session.image.close()
        except Exception:
            pass


def _crop(image_bgr: np.ndarray, region) -> Image.Image:
    h, w = image_bgr.shape[:2]
    pad_x = region.width * CROP_PADDING_FRACTION
    pad_y = region.height * CROP_PADDING_FRACTION
    x0 = max(0, int(region.x - pad_x))
    y0 = max(0, int(region.y - pad_y))
    x1 = min(w, int(region.x + region.width + pad_x))
    y1 = min(h, int(region.y + region.height + pad_y))
    crop_bgr = image_bgr[y0:y1, x0:x1]
    return Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB))


class PipelineInputError(Exception):
    """Raised for problems with the input itself (bad image, etc.) --
    handled identically to an unexpected crash (status -> failed with a
    message) but keeps the two situations distinguishable in logs."""


def run_pipeline(scan_session: ScanSession) -> ScanSession:
    scan_session.status = "processing"
    scan_session.save(update_fields=["status"])

    try:
        _run_pipeline_inner(scan_session)
        scan_session.status = "done"
    except PipelineInputError as exc:
        scan_session.status = "failed"
        scan_session.error_message = str(exc)
    except Exception as exc:  # last-resort net -- see module docstring
        logger.exception("pipeline crashed for scan %s", scan_session.id)
        scan_session.status = "failed"
        scan_session.error_message = f"{type(exc).__name__}: {exc}"
    finally:
        from django.utils import timezone

        scan_session.completed_at = timezone.now()
        scan_session.save()

    return scan_session


def _run_pipeline_inner(scan_session: ScanSession) -> None:
    image_bgr = _load_bgr(scan_session)
    if image_bgr is None:
        raise PipelineInputError("Could not decode the uploaded image. Is it a valid photo file?")

    # --- Stage 1: local model finds candidate spine regions -----------
    t0 = time.perf_counter()
    detector = TesseractSpineDetector()
    regions = detector.detect(image_bgr)
    scan_session.local_model_ms = (time.perf_counter() - t0) * 1000

    if not regions:
        # Zero books detected is a normal, valid outcome -- not a
        # failure. The scan completes with an empty result set and the
        # frontend shows "we didn't find any spines" rather than an error.
        scan_session.status = "done"
        return

    vlm = get_vlm_client()
    catalog = get_catalog_entries()
    auto_threshold = settings.MATCH_AUTO_ACCEPT_THRESHOLD
    review_floor = settings.MATCH_REVIEW_THRESHOLD

    vlm_total_ms = 0.0
    vlm_calls = 0
    total_cost = 0.0

    for region in regions:
        crop = _crop(image_bgr, region)

        detected = DetectedBook.objects.create(
            scan_session=scan_session,
            bbox_x=region.x, bbox_y=region.y,
            bbox_width=region.width, bbox_height=region.height,
            detector_confidence=region.confidence,
        )
        _save_crop(detected, crop)

        # --- Stage 2: hosted VLM reads the crop ------------------------
        read = vlm.read_spine(crop)
        vlm_total_ms += read.latency_ms
        vlm_calls += 1
        total_cost += read.estimated_cost_usd

        if not read.ok:
            detected.read_error = read.error
            detected.review_status = "pending_review"
            detected.save()
            continue

        detected.read_title = read.title
        detected.read_author = read.author

        # --- Stage 3: match against the catalog -------------------------
        result = match_catalog(read.title, read.author, catalog)
        tier = result.tier(auto_threshold, review_floor)
        detected.match_tier = tier
        detected.match_alternates = [
            {
                "catalog_id": c.catalog_id, "title": c.title, "author": c.author,
                "score": c.score,
            }
            for c in result.candidates
        ]

        if result.best:
            detected.match_catalog_id = result.best.catalog_id
            detected.match_title = result.best.title
            detected.match_author = result.best.author
            detected.match_score = result.best.score

        if tier == "auto":
            detected.review_status = "auto_added"
            detected.final_title = result.best.title
            detected.final_author = result.best.author
            detected.save()
            LibraryBook.objects.create(
                title=result.best.title,
                author=result.best.author,
                catalog_id=result.best.catalog_id,
                source_detected_book=detected,
                was_auto_added=True,
                match_score_at_add=result.best.score,
            )
        else:
            detected.review_status = "pending_review"
            detected.save()

    scan_session.vlm_total_ms = vlm_total_ms
    scan_session.vlm_call_count = vlm_calls
    scan_session.estimated_cost_usd = total_cost


def _save_crop(detected: DetectedBook, crop: Image.Image) -> None:
    import io

    from django.core.files.base import ContentFile

    try:
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="JPEG", quality=85)
        detected.crop_image.save(f"{detected.id}.jpg", ContentFile(buf.getvalue()), save=True)
    except Exception:
        # A crop-save failure shouldn't take down the whole scan --
        # the review screen just won't have a thumbnail for this one.
        logger.exception("failed to persist crop image for detected book %s", detected.id)
