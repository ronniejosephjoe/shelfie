"""
Local, pretrained, CPU-only model: finds candidate book-spine regions in
a bookshelf photo. This is step 3 of the pipeline (local model finds
spines; step 4, the hosted VLM, reads them -- see vlm_client.py for why
the split is where it is).

Implementation note (this mattered enough to write down): the obvious
choice here is a COCO-pretrained object detector (YOLOv8n's 'book'
class is a very standard answer). I built the code path for it and cut
it -- see README "Key decisions" for the actual reasoning, which came
down to dependency weight, not capability: pulling in torch to run one
small CPU model added ~1-2GB of transitive CUDA dependencies from
PyPI's default Linux wheel (no CPU-only wheel is available without a
separate package index), for a model whose job here is coarse
localization, not classification accuracy.

Instead this uses Tesseract -- also a pretrained model (an LSTM text
recognizer), already CPU-native, and already required elsewhere in this
repo as a fallback OCR path (see vlm_client.py) -- purely for TEXT
REGION localization. A book spine is, almost by definition, a vertical
strip of text on a mostly-uniform background, so "where is there text"
is a reasonable proxy for "where is there a spine." We deliberately
throw away Tesseract's own OCR *guess* at this stage (it's often poor on
rotated/stylized spine text) and keep only the bounding boxes; the
actual title/author read is delegated entirely to the hosted VLM in the
next stage. That division of labor is the "local vs. hosted routing"
the brief asks us to be explicit about.

Known gap from this choice: a spine with no legible text (blank, or a
design-only cover) will not be detected at all. Documented in the
README under "what's unfinished."
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger("shelfie")

# Below this per-word OCR confidence (Tesseract's own 0-100 scale), we
# don't trust the box enough to even count it as "there's text here."
MIN_WORD_CONFIDENCE = 25

# Word boxes closer than this (as a fraction of image width) get merged
# into the same spine-region candidate.
MERGE_GAP_FRACTION = 0.015

# Final candidate boxes smaller than this (as a fraction of image area)
# are almost certainly OCR noise (a stray character), not a spine.
MIN_REGION_AREA_FRACTION = 0.0008


@dataclass
class SpineRegion:
    x: float
    y: float
    width: float
    height: float
    confidence: float  # 0..1, mean OCR word confidence in this region

    def to_bbox_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def _ocr_word_boxes(image_bgr: np.ndarray) -> list[dict]:
    """Run Tesseract and return word-level boxes above MIN_WORD_CONFIDENCE."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    # PSM 11: "sparse text -- find as much text as possible in no
    # particular order." Bookshelf photos are exactly this: scattered,
    # differently-oriented text fragments, not a paragraph.
    config = "--psm 11"
    data = pytesseract.image_to_data(
        Image.fromarray(gray), config=config, output_type=pytesseract.Output.DICT
    )
    boxes = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        conf = float(data["conf"][i])
        if not text or conf < MIN_WORD_CONFIDENCE:
            continue
        boxes.append(
            {
                "x": data["left"][i],
                "y": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
                "conf": conf,
            }
        )
    return boxes


def _rotate(image_bgr: np.ndarray, angle: int) -> np.ndarray:
    if angle == 0:
        return image_bgr
    if angle == 90:
        return cv2.rotate(image_bgr, cv2.ROTATE_90_CLOCKWISE)
    if angle == 270:
        return cv2.rotate(image_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(angle)


def _map_box_back(box: dict, angle: int, orig_w: int, orig_h: int) -> dict:
    """Map a box found in a rotated image back to original-image coordinates."""
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    if angle == 0:
        return {"x": x, "y": y, "width": w, "height": h, "conf": box["conf"]}
    if angle == 90:
        # rotated_w = orig_h, rotated_h = orig_w
        return {"x": y, "y": orig_h - (x + w), "width": h, "height": w, "conf": box["conf"]}
    if angle == 270:
        return {"x": orig_w - (y + h), "y": x, "width": h, "height": w, "conf": box["conf"]}
    raise ValueError(angle)


def _merge_boxes(boxes: list[dict], img_w: int, img_h: int) -> list[SpineRegion]:
    """Greedy merge of nearby/overlapping word boxes into spine-region candidates."""
    if not boxes:
        return []

    gap = MERGE_GAP_FRACTION * max(img_w, img_h)
    # Each box starts as its own cluster; repeatedly merge any pair whose
    # expanded rectangles intersect, until stable. Fine for the box
    # counts we see per photo (tens, not thousands).
    clusters = [dict(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        merged = []
        used = [False] * len(clusters)
        for i, a in enumerate(clusters):
            if used[i]:
                continue
            ax0, ay0 = a["x"] - gap, a["y"] - gap
            ax1, ay1 = a["x"] + a["width"] + gap, a["y"] + a["height"] + gap
            for j in range(i + 1, len(clusters)):
                if used[j]:
                    continue
                b = clusters[j]
                bx0, by0 = b["x"], b["y"]
                bx1, by1 = b["x"] + b["width"], b["y"] + b["height"]
                overlap = not (bx1 < ax0 or bx0 > ax1 or by1 < ay0 or by0 > ay1)
                if overlap:
                    nx0, ny0 = min(ax0 + gap, bx0), min(ay0 + gap, by0)
                    nx1, ny1 = max(ax1 - gap, bx1), max(ay1 - gap, by1)
                    a = {
                        "x": nx0,
                        "y": ny0,
                        "width": nx1 - nx0,
                        "height": ny1 - ny0,
                        "conf": (a["conf"] + b["conf"]) / 2,
                    }
                    ax0, ay0 = a["x"] - gap, a["y"] - gap
                    ax1, ay1 = a["x"] + a["width"] + gap, a["y"] + a["height"] + gap
                    used[j] = True
                    changed = True
            merged.append(a)
            used[i] = True
        clusters = merged

    regions = []
    min_area = MIN_REGION_AREA_FRACTION * img_w * img_h
    for c in clusters:
        x = max(0.0, c["x"])
        y = max(0.0, c["y"])
        w = min(c["width"], img_w - x)
        h = min(c["height"], img_h - y)
        if w * h < min_area:
            continue
        regions.append(SpineRegion(x=x, y=y, width=w, height=h, confidence=min(c["conf"] / 100, 1.0)))
    return regions


def _iou(a: SpineRegion, b: SpineRegion) -> float:
    ax0, ay0, ax1, ay1 = a.x, a.y, a.x + a.width, a.y + a.height
    bx0, by0, bx1, by1 = b.x, b.y, b.x + b.width, b.y + b.height
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union else 0.0


def _dedupe_across_rotations(regions: list[SpineRegion], iou_threshold: float = 0.15) -> list[SpineRegion]:
    regions = sorted(regions, key=lambda r: r.confidence, reverse=True)
    kept: list[SpineRegion] = []
    for r in regions:
        if all(_iou(r, k) < iou_threshold for k in kept):
            kept.append(r)
    return kept


class TesseractSpineDetector:
    """Text-region-based spine detector. See module docstring for rationale."""

    def detect(self, image_bgr: np.ndarray) -> list[SpineRegion]:
        h, w = image_bgr.shape[:2]
        all_regions: list[SpineRegion] = []
        try:
            for angle in (0, 90, 270):
                rotated = _rotate(image_bgr, angle)
                boxes = _ocr_word_boxes(rotated)
                mapped = [_map_box_back(b, angle, w, h) for b in boxes]
                all_regions.extend(_merge_boxes(mapped, w, h))
        except Exception:
            # Tesseract missing/misconfigured, corrupt image, etc. --
            # detection failing should degrade to "zero spines found,"
            # not crash the request. The API layer turns zero spines
            # into a clear ScanSession status, not a 500.
            logger.exception("spine detection failed; returning no regions")
            return []

        deduped = _dedupe_across_rotations(all_regions)
        return sorted(deduped, key=lambda r: r.x)
