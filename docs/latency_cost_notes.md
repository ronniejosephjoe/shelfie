# Latency & cost notes

Working notes behind the numbers quoted in the top-level README. Kept
separate so the README stays readable; this is the "show your work."

## Local model (measured, real)

`python scripts/bench_pipeline.py`, run against all four `photos/*.jpg`,
on the machine this was built on (single CPU core, no GPU):

```
photo                        regions  local_ms  vlm_ms_total   auto  review  unmatched  errors
----------------------------------------------------------------------------------------------
01_clean_horizontal.jpg            5       376           374      0       2          3       0
02_vertical_spines.jpg             4       346           235      0       0          3       1
03_messy_shelf.jpg                 3       271           164      0       0          2       1
04_low_light_blur.jpg              3       376           147      0       0          0       3
----------------------------------------------------------------------------------------------
TOTAL / photo (avg)              3.8       342           230      0       2          8       5

local model: 91.2 ms per detected region (Tesseract, 3 rotation passes)
```

`local_model_ms` is wall-clock for `TesseractSpineDetector.detect()`:
three full OCR passes (0/90/270 degrees) over the whole image plus
region merging/deduping. ~340ms/photo on a shelf with 8-9 painted
spines. This scales roughly linearly with image size and is dominated
by the 3x OCR passes, not the merge step -- the obvious speedup if this
needed to be faster is running the three rotations in parallel
(they're independent) rather than optimizing the merge logic.

## Detector recall (measured, real, and the most important honest number here)

The four photos contain 8, 8, 9, and 8 painted spines respectively.
Detected regions were 5, 4, 3, 3. That's rough, not a typo: recall is
somewhere around 40-50% on these synthetic images. Two known,
specific causes, not a mystery:

1. Text set in a low-contrast color close to its background spine
   color (deliberately included in `03_messy_shelf.jpg`) often falls
   below Tesseract's per-word confidence floor entirely.
2. Adjacent thin spines with text close to each other get merged into
   one region by the gap-merge step (`MERGE_GAP_FRACTION` in
   `spine_detector.py`) more often than intended -- worth tightening
   given more time, see README "what's unfinished."

## VLM reads (measured, but from the *mock* provider -- read this caveat first)

No funded API key was available in the environment this was built in,
so `VLM_PROVIDER=mock` (Tesseract OCR on each crop, single pass) is
what actually produced the `read_title`/`read_author` values above, not
a hosted call. For example, from `01_clean_horizontal.jpg`:

```
read=('- 7', '') -> match='The 7 Habits of Highly Effective People' score=0.509 tier=unmatched
read=('| The Hobbit', '') -> match='The Hobbit' score=0.85 tier=review
read=('Atomic H:', '') -> match='Atomic Habits' score=0.6346 tier=review
```

This is genuinely useful data, just not the data the "Local vs. hosted
routing" section of the brief is asking for -- it's evidence for *why*
the hosted VLM step exists (single-pass Tesseract on a small, often
sideways crop is visibly worse than the full detector's 3-rotation
approach, let alone a modern hosted VLM), not a measurement of the
hosted VLM's own latency/accuracy. Rerun with a real `OPENAI_API_KEY`
and `VLM_PROVIDER=openai` in `.env` to get that; `OpenAIVisionClient`
already records `latency_ms` and `estimated_cost_usd` per call from the
live response, no code changes needed.

## VLM reads and cost (measured, real -- Gemini free tier, actual key)

Unlike every other VLM number in this doc, this one is a real live
measurement: `VLM_PROVIDER=gemini` against a real (free-tier)
`GEMINI_API_KEY`, run against `photos/pexels-photo-19582452.jpg` -- an
actual phone-camera bookshelf photo, not one of the four synthetic
test photos above.

Two real problems surfaced only by doing this, not by reading docs:

1. **First attempt used `gemini-3.6-flash` and failed almost
   entirely.** 26 spines detected -> 26 sequential VLM calls -> every
   call from roughly the sixth onward came back HTTP 429. The error
   body named the exact quota:
   `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value **20**
   -- a hard 20-requests-*per day* cap for that specific model on the
   free tier, not a per-minute rate. No amount of in-request
   retry/backoff can wait that out. This is now handled two ways in
   `GeminiVisionClient` (see its docstring in `vlm_client.py`):
   proactive throttling between calls (`GEMINI_MIN_CALL_INTERVAL_SECONDS`,
   default 4.5s) plus a bounded retry-with-backoff on 429 that honors
   the API's `Retry-After` hint -- and a distinct `error="rate_limited"`
   classification once retries are exhausted, instead of lumping it
   into the generic `api_error` bucket.
2. **Switched the default model to `gemini-3.1-flash-lite`** after
   verifying it directly against the same real key: correct read on
   the first test crop (`{"title": "God", "author": "Reza Aslan"}`,
   matching the actual spine), and a free-tier daily allowance that
   comfortably covers a 26-spine scan where `gemini-3.6-flash`'s did
   not. `gemini-2.5-flash` was also tried and rejected -- returns
   `404 This model ... is no longer available to new users`.

Full run against the real photo, post-fix, `gemini-3.1-flash-lite`:

```
local_model_ms    : 1387.3   (Tesseract detection, 26 regions found)
vlm_call_count    : 26
vlm_total_ms      : 192118.1  (includes ~4.5s/call proactive throttle --
                                see below for what that buys)
estimated_cost_usd: 0.00     (free tier; GEMINI_BILLING_ENABLED=false)

read outcomes: 22 successful reads, 3 "unreadable" (blank/no
candidates -- likely spine crops with no legible text, e.g. a
cluster-merge artifact), 1 "timeout", 0 "rate_limited"
```

The 22 successful reads are real, specific, and correct as far as they
can be checked by eye against a general-nonfiction/political-history
shelf: `"A Thousand Days"` / Arthur M. Schlesinger Jr., `"The Decline
and Fall of the Roman Empire"` / Edward Gibbon, `"Billy Bathgate"` /
E.L. Doctorow, `"A Farewell to Arms"` / Ernest Hemingway, `"The Burden
of Proof"` / Scott Turow, `"Statistics: An Introductory Analysis"` /
Taro Yamane -- all real, correctly-titled books, none of them
mis-transcribed nonsense.

**None of them auto-matched, and this is a catalog coverage problem,
not a pipeline bug.** `catalog.csv`'s 134 entries are deliberately
bestseller/genre-fiction-weighted (see `scripts/build_catalog.py`) --
Game of Thrones, Klara and the Sun, 1984, Educated, Atomic Habits,
Lord of the Rings. A shelf of 1960s-80s political history and
statistics texts has essentially no real overlap with that catalog, so
`match_catalog()` correctly reports its best (weak) guess and the
review-tier thresholds correctly keep those guesses out of
auto-accept -- e.g. `"The Decline and Fall of the Roman Empire"`
scored 0.48 against `"A Song of Ice and Fire"` and landed
`unmatched`, which is the right call given no real match exists in
this catalog. Widening `catalog.csv` to include nonfiction/history
titles (several of the exact ones read above would be a natural start)
would fix this for real without touching any matching or detection
code -- flagged as unfinished, not done here, since it's a content
decision rather than a bug fix.

## VLM cost (calculated from OpenAI's published pricing/tokenization, not measured live)

Pricing as of when this was written (August 2026): gpt-4o-mini is
$0.15 / 1M input tokens, $0.60 / 1M output tokens.

Image input tokens for gpt-4o-mini follow `tokens = 2833 + 5667 x tiles`,
`tiles = ceil(w/512) x ceil(h/512)`. Every crop this pipeline sends is
well under 512px in both dimensions (single spine, tightly cropped), so
`tiles = 1` for essentially every request:

```
input tokens  = 2833 + 5667*1 = 8500
input cost    = 8500 / 1,000,000 * $0.15   = $0.001275
output cost   = ~50 tokens * $0.60 / 1M    = $0.00003   (small JSON reply)
---------------------------------------------------------
estimated cost per spine read              ~= $0.0013
```

For a shelf with N spines, estimated scan cost is `~= $0.0013 * N`
(one VLM call per detected region, per `pipeline.py`) -- about $0.013
for a 10-book shelf, ~$0.03 for a full 25-book shelf. This is a
calculation from published rates, not a live measurement; it will be
off to the extent OpenAI's pricing or tokenization has changed since,
or crops end up larger than expected. `estimated_cost_usd` on
`ScanSession`/`DetectedBook` is computed the same way in code
(`vlm_client.OPENAI_PRICING_PER_1M`) so it's one place to update if
pricing changes, and once a real key is in use the number becomes an
actual measurement (from `response.usage`) rather than this estimate.

## VLM latency (published/typical, not measured live)

Community-reported gpt-4o-mini latency for small vision requests
(short prompt, one small image, short JSON response) is commonly in
the ~1-3 second range end to end. Not independently verified against a
live call in this environment -- flagged as an estimate, not dressed
up as a measurement, unlike the local-model and cost numbers above.
For N spines called sequentially (current implementation, see
"what's unfinished" in the README) that's roughly `1.5s * N` added to
a scan; the obvious fix if this matters is calling the VLM concurrently
across detected regions rather than in the current for-loop.
