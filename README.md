# Shelfie

A photo of a bookshelf, in. A structured personal library, out.

Built for the "Shelfie" take-home task (Full Stack Developer, AI &
Computer Vision) in the ~8-hour scope described in the brief. This
README covers setup, architecture, the measured numbers, how the
catalog was built, the decisions I made under the time box, and what's
still unfinished.

## The flow

1. Expo app: take or pick a photo of a bookshelf.
2. Django REST API: photo comes in.
3. Local, pretrained, CPU-only model finds candidate spine regions.
4. Hosted vision-language model reads title/author off each region.
5. Each read is matched against `catalog.csv` with a confidence score.
6. High-confidence matches are added automatically. Everything else --
   low confidence, ambiguous, unmatched, or unreadable -- goes to a
   review step where the user confirms, corrects, or discards it.
7. Confirmed books persist to a library list.

## Setup and run (from a clean clone)

### Backend

```
cd backend
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Tesseract is a system dependency (used by the local spine detector and
by the offline VLM fallback -- see "Local vs. hosted routing" below),
not a Python package:

```
# macOS
brew install tesseract
# Debian/Ubuntu
sudo apt-get install tesseract-ocr
```

Then:

```
cp .env.example .env               # defaults work as-is (VLM_PROVIDER=mock)
python manage.py migrate
python manage.py load_catalog      # loads ../catalog.csv into the DB
python manage.py runserver 0.0.0.0:8000
```

To use a real hosted VLM instead of the offline mock, set in `.env`
(two supported providers -- pick one):

```
# OpenAI (paid API, requires billing enabled on the account)
VLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Gemini (Google AI Studio has a genuine free tier -- no credit card
# needed, get a key at https://aistudio.google.com/apikey)
VLM_PROVIDER=gemini
GEMINI_API_KEY=...
```

### Frontend

```
cd frontend
npm install
cp .env.example .env
```

Edit `.env`'s `EXPO_PUBLIC_API_BASE_URL` to point at the backend:
- Expo web or an iOS simulator on the same machine as the backend:
  `http://localhost:8000` works as-is.
- A physical phone via Expo Go: needs your computer's LAN IP (e.g.
  `http://192.168.1.23:8000`) -- the phone is a different device on
  the network. `ipconfig getifaddr en0` (macOS) or `hostname -I`
  (Linux) will get it.

```
npx expo start
```

Then press `i` / `a` for a simulator, `w` for web, or scan the QR code
in Expo Go.

### Tests

```
cd backend
pytest
```

30 tests: matching-engine unit tests (`catalog/tests/test_matching.py`),
pipeline graceful-failure tests with the detector/VLM swapped for
deterministic fakes (`scanner/tests/test_pipeline.py`), and API-level
tests (`scanner/tests/test_api.py`).

## Architecture

```
Expo app  --(multipart photo)-->  Django REST API  (POST /api/scans/)
                                        |
                                        v
                          scanner/services/pipeline.py
                                        |
                    +-------------------+-------------------+
                    v                                        v
        TesseractSpineDetector                    (per detected region)
        (local, CPU, pretrained)                  OpenAIVisionClient /
        finds candidate spine regions              MockVLMClient
                    |                              reads title/author
                    +-------------------+-------------------+
                                        v
                          catalog/matching.py
                fuzzy-matches read against catalog.csv,
                          returns a confidence score
                                        |
                    +-------------------+-------------------+
                    v                                        v
           confidence >= AUTO_ACCEPT               otherwise: DetectedBook
           -> auto-added to LibraryBook              stays pending_review
                                                       (Expo review screen)
```

Backend apps:
- `catalog/` -- `CatalogBook` model, `catalog.csv` loader, and the
  matching engine (`matching.py`, DB-independent and unit-testable on
  its own).
- `scanner/` -- the scan pipeline: `ScanSession` / `DetectedBook` /
  `LibraryBook` models, the local detector and VLM client services, and
  the API views that tie it together.

Frontend: three screens (Capture, Review, Library), no router library
-- see `frontend/App.js`'s comment for why. `frontend/src/api.js` is the
whole backend contract.

## Measured latency & cost

Full numbers and methodology in `docs/latency_cost_notes.md`
(`scripts/bench_pipeline.py` reproduces them). Summary:

| Stage | Number | Basis |
|---|---|---|
| Local model (Tesseract, 3 rotation passes) | ~91ms / detected region, ~340ms / photo | **Measured**, this machine, CPU only |
| Local model recall | ~40-50% of painted spines detected on the test photos | **Measured** -- see below, the least flattering number in this repo |
| Hosted VLM cost (gpt-4o-mini) | ~$0.0013 / spine read (~1.3 cents for a 10-book shelf) | **Calculated** from OpenAI's published pricing ($0.15/$0.60 per 1M tokens) and image-tokenization formula (2833 + 5667/tile; every crop here is 1 tile) |
| Hosted VLM latency (gpt-4o-mini) | ~1-3s / call (typical) | **Estimated** from published/community reports, not measured live -- no OpenAI key used in this build |
| Hosted VLM read + cost (Gemini free tier, `gemini-3.1-flash-lite`) | 26 spines: 22 successful reads, 3 unreadable, 1 timeout, 0 rate-limited; ~7.4s/call incl. throttle; **$0.00** (free tier) | **Measured**, real key, real photo (`photos/pexels-photo-19582452.jpg`) -- see `docs/latency_cost_notes.md` for the full run, including the wrong-model-first-try story |

I did not have a funded `OPENAI_API_KEY` while building this, so the
OpenAI row above is calculated/estimated, not measured, and
`docs/latency_cost_notes.md` says so explicitly next to each number
rather than presenting everything with the same false precision. The
Gemini row, by contrast, is a real measurement against a real free-tier
key -- including a genuine failure found only by doing that: the
first model tried (`gemini-3.6-flash`) turned out to cap free-tier
usage at **20 requests per day**, confirmed directly from the API's own
error body, not documentation. `gemini-3.1-flash-lite` (now the
default) was verified against the same key before being adopted.
`OpenAIVisionClient` and `GeminiVisionClient` both compute
`estimated_cost_usd` from the real response on every call, so the
number in the UI and in `ScanSession.estimated_cost_usd` is always an
actual measurement for whichever provider is actually configured, not
a different code path than what's described here.

### Local vs. hosted routing, and why the split is where it is

The local model's job is narrowed to **localization** (where in the
photo is there a spine), not **reading** (what does it say). That's a
deliberate choice, not the default: reading small, rotated, stylized
spine text is exactly the kind of task a modern hosted VLM is
dramatically better at than a small CPU-only local model, and pushing
localization instead of reading onto the local stage means the
expensive/slow hosted call only ever runs on a small, already-cropped
region -- not the whole photo.

I originally planned the "obvious" local model here: YOLOv8n, using
COCO's `book` class, CPU inference via `ultralytics`. I built the
detector interface with that in mind, then cut it -- **not** because it
wouldn't work, but because of a concrete dependency-weight problem I
hit while building this: `torch`'s default Linux PyPI wheel pulls in
its CUDA runtime as declared pip dependencies (`nvidia-cublas`,
`nvidia-cufft`, etc.), adding well over a gigabyte of GPU libraries
libraries you'll never use for a CPU-only model that only needs to
localize text regions. `download.pytorch.org`'s CPU-only wheel index
would avoid that, but isn't reachable from every network. Rather than
ship a `pip install` step that's slow or flaky depending on the
grader's setup, I used **Tesseract** -- also a pretrained model (an
LSTM text recognizer), CPU-native, and a dependency I needed anyway for
the offline VLM fallback (see below) -- purely to find text regions,
discarding its own OCR guess. `scanner/services/spine_detector.py`'s
module docstring has the full reasoning. Given more time, I'd swap in
YOLOv8n (or a similarly small COCO detector) behind the same
`SpineDetector` interface and A/B the two on real photos -- see
"What's unfinished."

The hosted VLM client (`scanner/services/vlm_client.py`) supports two
real providers behind one interface, picked via `VLM_PROVIDER`:
`openai` (`gpt-4o-mini` by default, configurable via
`OPENAI_VISION_MODEL`) and `gemini` (`gemini-3.1-flash-lite` by
default, configurable via `GEMINI_VISION_MODEL`). Gemini was added
specifically because Google AI Studio offers a genuine no-credit-card
free tier for Flash-class models, which OpenAI's API does not -- the
lower-friction option if you don't already have a funded API account.
That default model wasn't the first one tried: `gemini-3.6-flash`
looked reasonable from the model list but its free tier turned out to
allow only 20 requests/day (found by actually running a 26-spine real
photo through it and reading the 429 error body, which names the exact
quota), which no client-side retry/backoff can wait out for a scan
that needs more calls than that in one run. `GeminiVisionClient` also
proactively throttles calls (`GEMINI_MIN_CALL_INTERVAL_SECONDS`) and
retries a bounded number of times on a transient 429 before reporting
a distinct `rate_limited` error -- see its docstring and
`docs/latency_cost_notes.md` for the full story. Both providers are
implemented and unit-tested (`scanner/tests/test_vlm_client.py`
mocks the network layer to exercise success, malformed JSON, safety-
blocked/empty responses, HTTP errors, rate limits, and timeouts for
each). When `VLM_PROVIDER=mock` (the default, and what runs with zero
setup or API key) it falls back to a single Tesseract OCR pass on the
crop instead -- clearly worse than a real hosted read, and its
confidence is hard-capped below the auto-accept threshold so a mock
read can never sneak through as a silent auto-add. This exists so the
full pipeline, including the review screen, is demoable without any
key.

## How the catalog was built

`catalog.csv`, generated by `scripts/build_catalog.py` (re-run it after
editing the `ROWS` list -- it's meant to be a maintained fixture, not a
hand-edited CSV). 134 entries, weighted towards books people actually
own (Harry Potter, LOTR, contemporary bestseller fiction/nonfiction)
rather than obscure titles, per the brief's "we'll hand you our
shelves" note.

Deliberate messiness, and where it lives:
- **Duplicate editions of the same book**: `The Hobbit` (1937 paperback
  and a 2012 movie tie-in), `The Silmarillion` (1977 and a 1999
  illustrated edition), `Atomic Habits`, `Gone Girl`, and a second
  Harry Potter #1 entry (illustrated hardcover).
- **US/UK title splits**: `Harry Potter and the Philosopher's Stone` /
  `...Sorcerer's Stone`; `Northern Lights` / `The Golden Compass`.
- **Two different books sharing an exact title**: `The Alchemist`
  (Paulo Coelho vs. Michael Scott), `Nightfall` (Isaac Asimov vs. Jake
  Halpern).
- **Omnibus editions alongside individual volumes**: The Lord of the
  Rings (one-volume omnibus + the three individual books), the
  Foundation Trilogy, the Chronicles of Narnia, A Song of Ice and Fire.
- **Titles that are substrings of other titles**: `Dune` / `Dune
  Messiah` / `Children of Dune`; `Gone` / `Gone Girl`.
- **Author names in multiple forms**: initials spacing (`George R.R.
  Martin` vs. `George R. R. Martin`), accents (`Gabriel Garcia Marquez`
  vs. `Gabriel García Márquez`), transliteration (`Dostoevsky` vs.
  `Dostoyevsky`), and `Lastname, Firstname` order throughout the
  `author_alt` column.

`catalog/matching.py`'s docstring and `catalog/tests/test_matching.py`
explain and test how each of these is actually handled (in short:
`rapidfuzz` token-set matching on normalized text handles reordering
and partial overlap; a length-dampening term specifically stops
`token_set_ratio`'s "subset = perfect score" behavior from making
`Dune` and `Dune Messiah` tie; author similarity is what disambiguates
the two `The Alchemist` entries, and when no author was read at all,
the matcher correctly reports a near-tie between them rather than
guessing).

## Troubleshooting

**Local model finds zero spines on every photo, on macOS.** This bit me
during my own live test of this exact repo on a Mac (not a hypothetical
-- see the commit that added `settings.py`'s `_SAFE_TMP_DIR`): macOS's
`/tmp` is a symlink to `/private/tmp`, and at least one Homebrew
tesseract/Leptonica build silently fails to read temp files written
through that symlink in some shell contexts, which `spine_detector.py`'s
graceful-failure handling then reports as "no spines found" instead of
surfacing as the environment bug it actually is. `settings.py` already
works around this by pointing Python's `tempfile` module at
`backend/tmp/` (a real directory inside the project) instead of trusting
whatever `$TMPDIR` resolves to -- if you're running an older clone
without that fix, `git pull` or check `shelfie_backend/settings.py` for
`_SAFE_TMP_DIR`.

## Key decisions and tradeoffs

- **Local model: Tesseract-based localization, not a COCO object
  detector.** Covered above -- a dependency-weight call, documented in
  `spine_detector.py`, with a clean interface to swap in YOLOv8n later.
- **Synchronous pipeline.** `pipeline.run_pipeline()` runs entirely
  inside the request/response cycle. For a single-demo-user 8-hour
  scope that's an acceptable simplification; a real product would move
  this to a task queue (Celery/RQ) so the upload returns immediately
  and the client polls or subscribes for progress, especially once VLM
  calls run one-at-a-time per spine (see "what's unfinished").
- **No authentication.** Explicitly out of scope per the brief ("what
  we're not grading"). The library is global, not per-user.
- **Mock VLM fallback instead of requiring a key to run at all.** Makes
  the whole pipeline, including the review UI, runnable and demoable
  immediately. Its output is visibly weaker and explicitly documented
  as such, not hidden behind a misleadingly-generic label.
- **Hand-rolled screen switching instead of React Navigation.** No
  simulator/device access in the environment this was built in to
  verify a native-dependency-heavy router actually works; three plain
  screens with two callback props each was the lower-risk choice. See
  `App.js`'s comment.
- **Synthetic test photos, disclosed as such.** No camera or a rights-
  clear way to source real bookshelf photos in this environment. Built
  to be genuinely testing (real catalog titles, varied
  orientation/contrast/legibility, deliberately blank and low-contrast
  spines) rather than trivial -- see `photos/`'s own notes and
  `scripts/generate_test_photos.py`. Real photos (handed over at the
  presentation, and ideally a real shelf beforehand) are what actually
  matters; these are the required committed "photos you tested with."
- **A scan that fails is still an HTTP 201, not a 5xx.**
  `ScanSession.status == "failed"` with a message in the body, so the
  client renders a specific failure state instead of special-casing
  transport errors. Only malformed *requests* (no file, non-image file,
  oversized file) are 400s. See `pipeline.py` and `views.py`.

## What's unfinished, and what I'd do with another day

- **Detector recall is the weakest measured number in this repo**
  (~40-50% of spines found on the synthetic test photos -- see
  `docs/latency_cost_notes.md`). Two identified, fixable causes: the
  region-merge step over-merges adjacent thin spines, and low-contrast
  spine text falls below Tesseract's confidence floor entirely. I'd
  tune `MERGE_GAP_FRACTION`/`MIN_WORD_CONFIDENCE` against real photos
  first, then try the YOLOv8n swap described above.
- **VLM calls run sequentially, one per detected spine.** Fine for a
  handful of spines; a full shelf would benefit from calling them
  concurrently (`asyncio`/`concurrent.futures`) rather than the current
  for-loop in `pipeline.py`.
- **`openai` still has no real measured numbers.** `gemini` now does
  (see the table above and `docs/latency_cost_notes.md`) -- a real
  free-tier key against a real 26-spine photo, including finding and
  fixing a hard daily-quota wall on the first model tried. `openai`
  remains unit-tested against mocked responses only
  (`scanner/tests/test_vlm_client.py`); one command away from real
  numbers with a funded `OPENAI_API_KEY` -- rerun
  `scripts/bench_pipeline.py` with `VLM_PROVIDER=openai` and replace
  the estimate in `docs/latency_cost_notes.md`.
- **Catalog has essentially no nonfiction/history coverage**, found by
  running a real nonfiction-heavy shelf through the live pipeline:
  every one of 22 successful Gemini reads was a real, correctly-read
  book (`"The Decline and Fall of the Roman Empire"` / Edward Gibbon,
  `"Billy Bathgate"` / E.L. Doctorow, etc.), and every single one
  landed `unmatched` or weak `review`, purely because `catalog.csv`'s
  134 entries are bestseller/genre-fiction-weighted (see "How the
  catalog was built" above). This is a content gap, not a matching or
  detection bug -- the confidence thresholds correctly kept these weak
  guesses out of auto-accept. Widening the catalog to include the
  actual titles read from this photo would be a quick, honest fix.
- **Crops aren't resized before the VLM call.** Every crop in the test
  photos is small enough to stay at 1 tile under gpt-4o-mini's pricing,
  but a real high-resolution phone photo's crops could be larger,
  changing the cost math in `docs/latency_cost_notes.md`. Worth
  explicitly downscaling crops to a known max size before sending.
- **No de-duplication against an existing library.** Re-scanning the
  same shelf twice adds the same books twice. Straightforward to add
  (check `LibraryBook` for an existing `catalog_id`/title+author before
  creating), cut for time.
- **React Navigation swap**, if a real nav stack (deep links, back
  gestures, etc.) becomes worth the added native dependencies.
- **Run on a real iOS Simulator, but not through a full scan there
  specifically.** `npx expo start --ios` launched the app on a real
  iPhone 16 Pro simulator (Expo Go, SDK 57.0.0, matching this project
  exactly), bundled cleanly, and rendered the Capture screen --
  confirmed with a screenshot of the actual running simulator, not
  just a clean `expo export` exit code (which is all that had been
  verified earlier -- see AI_USAGE.md for both). The identical code
  path has been run end to end (capture -> review -> library, real
  photo, real Gemini key) through the Expo web preview; a full pass
  through the iOS Simulator specifically, and any Android testing,
  has not been done.
