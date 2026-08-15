"""
Hosted vision-language model client: step 4 of the pipeline. Takes one
cropped spine image (from the local detector) and reads a title/author
off it.

Why this step is hosted and not local: reading small, rotated, stylized
spine text is exactly the kind of open-vocabulary, contextual reading
task current CPU-cheap local models are weak at, and where a modern
hosted VLM is dramatically better -- that gap (not raw compute) is the
actual justification for the local/hosted split, and it's why the local
stage's job was narrowed to *localization* rather than reading (see
spine_detector.py's docstring for the other half of this decision).

Two implementations behind one interface:
  - OpenAIVisionClient: real hosted call (OpenAI gpt-4o-mini vision by
    default -- picked for a strong price/latency/accuracy balance
    among hosted multimodal models at time of writing, see README for
    measured numbers). Requires OPENAI_API_KEY.
  - MockVLMClient: offline stand-in used when no key is configured
    (VLM_PROVIDER=mock, the default). Runs local Tesseract OCR on the
    crop as a rough approximation so the full pipeline is demoable
    without any API key or network call -- it is NOT a substitute for
    the real hosted read and is visibly worse on rotated/stylized text.
    This is disclosed here and in AI_USAGE.md / README, not hidden.

Graceful failure is the point of this module as much as the read
itself: a timeout, a malformed/non-JSON response, or an empty read must
turn into a DetectedBook row with read_error set -- never an exception
that takes down the request, and never a silently-invented title.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass

from django.conf import settings
from PIL import Image

logger = logging.getLogger("shelfie")

PROMPT = """You are reading a single cropped photo of one book's spine \
(or a small cluster of at most a couple of spines). Identify the title \
and author printed on it.

Respond with ONLY a JSON object, no markdown fences, no commentary:
{"title": "<best-guess title, or empty string if unreadable>",
 "author": "<best-guess author, or empty string if unreadable/absent>",
 "confidence": <float 0 to 1, your own confidence in this specific read>}

If the spine is blank, illegible, or not a book, return empty strings \
for title and author and a low confidence."""


@dataclass
class VLMReadResult:
    title: str = ""
    author: str = ""
    model_confidence: float | None = None
    error: str = ""  # "" on success; "timeout" | "api_error" | "malformed_json" | "unreadable"
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    provider: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _crop_to_data_url(crop: Image.Image) -> str:
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _extract_json(raw: str) -> dict | None:
    """Best-effort salvage of a JSON object from a model response.

    Models occasionally wrap JSON in markdown fences or add a stray
    sentence despite instructions. We try strict parsing first, then
    fall back to grabbing the first {...} block before giving up.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


# Pricing as of writing (see README "Local vs. hosted routing" for the
# source/date this was checked). Per-1M-token USD.
OPENAI_PRICING_PER_1M = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


class OpenAIVisionClient:
    provider_name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float | None = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model or settings.OPENAI_VISION_MODEL
        self.timeout = timeout or settings.VLM_TIMEOUT_SECONDS

    def _client(self):
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, timeout=self.timeout)

    def read_spine(self, crop: Image.Image) -> VLMReadResult:
        t0 = time.perf_counter()
        try:
            client = self._client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": {"url": _crop_to_data_url(crop)}},
                        ],
                    }
                ],
                max_tokens=200,
                timeout=self.timeout,
            )
        except Exception as exc:  # openai raises several exception types; any of
            # them here means "the hosted call didn't work," which is the one
            # thing this method must never propagate as an unhandled 500.
            elapsed = (time.perf_counter() - t0) * 1000
            is_timeout = "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower()
            logger.warning("VLM call failed (%s): %s", type(exc).__name__, exc)
            return VLMReadResult(
                error="timeout" if is_timeout else "api_error",
                latency_ms=elapsed,
                provider=self.provider_name,
            )

        elapsed = (time.perf_counter() - t0) * 1000
        raw = response.choices[0].message.content if response.choices else None
        parsed = _extract_json(raw)

        usage = getattr(response, "usage", None)
        cost = 0.0
        if usage:
            pricing = OPENAI_PRICING_PER_1M.get(self.model)
            if pricing:
                cost = (
                    usage.prompt_tokens / 1_000_000 * pricing["input"]
                    + usage.completion_tokens / 1_000_000 * pricing["output"]
                )

        if parsed is None:
            return VLMReadResult(
                error="malformed_json", latency_ms=elapsed,
                estimated_cost_usd=cost, provider=self.provider_name,
            )

        title = (parsed.get("title") or "").strip()
        author = (parsed.get("author") or "").strip()
        confidence = parsed.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        if not title:
            return VLMReadResult(
                error="unreadable", model_confidence=confidence, latency_ms=elapsed,
                estimated_cost_usd=cost, provider=self.provider_name,
            )

        return VLMReadResult(
            title=title, author=author, model_confidence=confidence,
            latency_ms=elapsed, estimated_cost_usd=cost, provider=self.provider_name,
        )


class MockVLMClient:
    """Offline stand-in: local Tesseract OCR on the crop, single best pass.

    Used automatically when VLM_PROVIDER=mock (the default) or no
    OPENAI_API_KEY is set, so the full pipeline -- including the review
    screen for low-confidence reads -- is demoable with zero setup. Not
    a claim that this is comparable in quality to the real hosted read;
    it generally is not (see README).
    """

    provider_name = "mock"

    def read_spine(self, crop: Image.Image) -> VLMReadResult:
        import pytesseract

        t0 = time.perf_counter()
        try:
            text = pytesseract.image_to_string(crop, config="--psm 7").strip()
        except Exception:
            logger.exception("mock VLM (tesseract) failed")
            return VLMReadResult(error="api_error", provider=self.provider_name)
        elapsed = (time.perf_counter() - t0) * 1000

        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return VLMReadResult(error="unreadable", latency_ms=elapsed, provider=self.provider_name)

        # Tesseract gives us undifferentiated text, not title/author
        # fields -- treat it all as a title guess with no author, which
        # is an honest representation of what this fallback actually
        # knows. Confidence is deliberately capped below auto-accept so
        # mock reads always land in review, never silently auto-added.
        return VLMReadResult(
            title=text, author="", model_confidence=0.4,
            latency_ms=elapsed, provider=self.provider_name,
        )


def get_vlm_client():
    if settings.VLM_PROVIDER == "openai" and settings.OPENAI_API_KEY:
        return OpenAIVisionClient()
    if settings.VLM_PROVIDER == "openai" and not settings.OPENAI_API_KEY:
        logger.warning("VLM_PROVIDER=openai but OPENAI_API_KEY is unset; falling back to mock")
    return MockVLMClient()
