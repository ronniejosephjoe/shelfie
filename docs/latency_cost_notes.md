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
