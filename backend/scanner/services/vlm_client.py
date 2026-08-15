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

Three implementations behind one interface:
  - OpenAIVisionClient: OpenAI gpt-4o-mini vision by default -- picked
    for a strong price/latency/accuracy balance among paid hosted
    multimodal models at time of writing. Requires OPENAI_API_KEY and
    a funded/billed OpenAI account.
  - GeminiVisionClient: Google's Gemini API (gemini-3.6-flash by
    default). Added specifically because Google AI Studio offers a
    genuine no-credit-card free tier for Flash-class models -- the
    lower-friction option if you don't already have a funded API
    account. Requires GEMINI_API_KEY (get one free at
    aistudio.google.com, no billing setup needed for the free tier).
    The free tier is rate-limited to single-digit-to-teens requests
    per minute (Google's published quotas vary by model), which this
    client actively manages -- see its class docstring below. This was
    found by actually running a 26-spine real photo through it: every
    call after the first several came back HTTP 429, not by reading
    the docs first.
  - MockVLMClient: offline stand-in used when neither is configured
    (VLM_PROVIDER=mock, the default). Runs local Tesseract OCR on the
    crop as a rough approximation so the full pipeline is demoable
    without any API key or network call -- it is NOT a substitute for
    a real hosted read and is visibly worse on rotated/stylized text.
    This is disclosed here and in AI_USAGE.md / README, not hidden.

Graceful failure is the point of this module as much as the read
itself: a timeout, a malformed/non-JSON response, a rate limit, or an
empty read must turn into a DetectedBook row with read_error set --
never an exception that takes down the request, and never a
silently-invented title.
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
    # "" on success; "timeout" | "api_error" | "malformed_json" |
    # "unreadable" | "rate_limited"
    error: str = ""
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    provider: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _crop_to_jpeg_bytes(crop: Image.Image) -> bytes:
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _crop_to_data_url(crop: Image.Image) -> str:
    b64 = base64.b64encode(_crop_to_jpeg_bytes(crop)).decode("ascii")
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


def _parsed_to_result(parsed: dict | None, elapsed: float, cost: float, provider: str) -> VLMReadResult:
    """Shared by every real provider once we have (or don't have) a
    parsed {title, author, confidence} dict -- keeps the
    malformed/unreadable/success classification identical across
    providers instead of duplicating it three times."""
    if parsed is None:
        return VLMReadResult(
            error="malformed_json", latency_ms=elapsed,
            estimated_cost_usd=cost, provider=provider,
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
            estimated_cost_usd=cost, provider=provider,
        )

    return VLMReadResult(
        title=title, author=author, model_confidence=confidence,
        latency_ms=elapsed, estimated_cost_usd=cost, provider=provider,
    )


# Pricing as of writing (see README "Local vs. hosted routing" for the
# source/date this was checked). Per-1M-token USD, paid-tier rates.
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
            logger.warning("OpenAI VLM call failed (%s): %s", type(exc).__name__, exc)
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

        return _parsed_to_result(parsed, elapsed, cost, self.provider_name)


# Paid-tier pricing, checked as of writing -- see README. Irrelevant on
# the free tier (the whole reason GeminiVisionClient exists here): free
# tier calls cost $0 and don't return billable usage the same way, so
# estimated_cost_usd is 0.0 unless GEMINI_BILLING_ENABLED is set,
# signaling you've moved off the free tier for real.
GEMINI_PRICING_PER_1M = {
    "gemini-3.6-flash": {"input": 1.50, "output": 7.50},
}


class GeminiVisionClient:
    """Google Gemini API, called directly over REST (no SDK dependency
    -- one predictable POST is simpler than chasing SDK version churn
    for a single endpoint). Added because Google AI Studio's free tier
    needs no credit card, which OpenAI's does -- see README.

    Free-tier quotas are low (single-digit-to-teens requests per
    minute, depending on model and account) and this pipeline reads
    every detected spine one at a time in a loop -- a single shelf
    photo with a dozen-plus books will blow through that quota well
    before the scan finishes. Found by actually running a real
    26-spine photo through this: every call from roughly the sixth
    onward came back HTTP 429. Two mitigations, both here rather than
    in the pipeline, so they apply no matter who calls this client:

      1. Proactive throttling (min_call_interval / GEMINI_MIN_CALL_
         INTERVAL_SECONDS): space calls out so most requests never hit
         the limit in the first place. Defaults to 4.5s -- roughly 13
         requests/minute, safely under the ~15 RPM ceiling of Google's
         most generous free-tier Flash models as of writing, and
         conservative for stricter ones. Set to 0 for a paid account
         with no meaningful RPM ceiling.
      2. Reactive backoff (_MAX_RETRIES): if a 429 slips through
         anyway (quota resets don't align perfectly with a fixed
         interval), back off and retry a bounded number of times,
         honoring the API's Retry-After header when present, before
         giving up and reporting error="rate_limited" -- a real,
         visible failure rather than a silent bad read.
    """

    provider_name = "gemini"
    _ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    _MAX_RETRIES = 2
    _RETRY_BASE_DELAY_SECONDS = 4.0
    _RETRY_MAX_DELAY_SECONDS = 20.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        min_call_interval: float | None = None,
    ):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_VISION_MODEL
        self.timeout = timeout or settings.VLM_TIMEOUT_SECONDS
        self.min_call_interval = (
            settings.GEMINI_MIN_CALL_INTERVAL_SECONDS if min_call_interval is None else min_call_interval
        )
        # Instance state on purpose: get_vlm_client() builds one client
        # per scan and this pipeline reuses it across every spine in
        # that scan, which is exactly the sequence we need to space out.
        self._last_call_at = 0.0

    def _throttle(self) -> None:
        if self.min_call_interval <= 0:
            return
        wait = self.min_call_interval - (time.monotonic() - self._last_call_at)
        if wait > 0:
            time.sleep(wait)

    def _retry_delay(self, response, attempt: int) -> float:
        retry_after = getattr(response, "headers", None)
        retry_after = retry_after.get("Retry-After") if retry_after else None
        if retry_after is not None:
            try:
                return min(float(retry_after), self._RETRY_MAX_DELAY_SECONDS)
            except (TypeError, ValueError):
                pass
        return min(self._RETRY_BASE_DELAY_SECONDS * (2**attempt), self._RETRY_MAX_DELAY_SECONDS)

    def read_spine(self, crop: Image.Image) -> VLMReadResult:
        import requests

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(_crop_to_jpeg_bytes(crop)).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 200},
        }

        self._throttle()
        t0 = time.perf_counter()
        response = None
        attempt = 0
        while True:
            try:
                response = requests.post(
                    self._ENDPOINT.format(model=self.model),
                    params={"key": self.api_key},
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.exceptions.Timeout:
                self._last_call_at = time.monotonic()
                elapsed = (time.perf_counter() - t0) * 1000
                return VLMReadResult(error="timeout", latency_ms=elapsed, provider=self.provider_name)
            except Exception as exc:
                self._last_call_at = time.monotonic()
                elapsed = (time.perf_counter() - t0) * 1000
                logger.warning("Gemini VLM call failed (%s): %s", type(exc).__name__, exc)
                return VLMReadResult(error="api_error", latency_ms=elapsed, provider=self.provider_name)

            if response.status_code == 429 and attempt < self._MAX_RETRIES:
                delay = self._retry_delay(response, attempt)
                logger.info(
                    "Gemini rate-limited (attempt %d/%d), backing off %.1fs",
                    attempt + 1, self._MAX_RETRIES, delay,
                )
                time.sleep(delay)
                attempt += 1
                continue
            break

        self._last_call_at = time.monotonic()
        elapsed = (time.perf_counter() - t0) * 1000

        if response.status_code == 429:
            logger.warning("Gemini still rate-limited after %d retries", self._MAX_RETRIES)
            return VLMReadResult(error="rate_limited", latency_ms=elapsed, provider=self.provider_name)

        if response.status_code != 200:
            logger.warning("Gemini API returned %s: %s", response.status_code, response.text[:300])
            return VLMReadResult(error="api_error", latency_ms=elapsed, provider=self.provider_name)

        try:
            data = response.json()
        except ValueError:
            return VLMReadResult(error="malformed_json", latency_ms=elapsed, provider=self.provider_name)

        usage = data.get("usageMetadata", {})
        cost = 0.0
        pricing = GEMINI_PRICING_PER_1M.get(self.model)
        if pricing and settings.GEMINI_BILLING_ENABLED:
            cost = (
                usage.get("promptTokenCount", 0) / 1_000_000 * pricing["input"]
                + usage.get("candidatesTokenCount", 0) / 1_000_000 * pricing["output"]
            )

        # A response can come back with zero candidates -- most often
        # the safety filter declining to describe the image, which is
        # a legitimate "couldn't read this" outcome, not a crash.
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            logger.info("Gemini returned no candidates (%s)", reason)
            return VLMReadResult(error="unreadable", latency_ms=elapsed, estimated_cost_usd=cost, provider=self.provider_name)

        try:
            raw = candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return VLMReadResult(error="malformed_json", latency_ms=elapsed, estimated_cost_usd=cost, provider=self.provider_name)

        parsed = _extract_json(raw)
        return _parsed_to_result(parsed, elapsed, cost, self.provider_name)


class MockVLMClient:
    """Offline stand-in: local Tesseract OCR on the crop, single best pass.

    Used automatically when VLM_PROVIDER=mock (the default) or no key
    is configured for the selected provider, so the full pipeline --
    including the review screen for low-confidence reads -- is
    demoable with zero setup. Not a claim that this is comparable in
    quality to a real hosted read; it generally is not (see README).
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
    provider = settings.VLM_PROVIDER

    if provider == "openai":
        if settings.OPENAI_API_KEY:
            return OpenAIVisionClient()
        logger.warning("VLM_PROVIDER=openai but OPENAI_API_KEY is unset; falling back to mock")

    elif provider == "gemini":
        if settings.GEMINI_API_KEY:
            return GeminiVisionClient()
        logger.warning("VLM_PROVIDER=gemini but GEMINI_API_KEY is unset; falling back to mock")

    return MockVLMClient()
