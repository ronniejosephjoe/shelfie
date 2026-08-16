# AI Usage

Short and honest, per the brief's ask.

This entire repository -- every file, every commit -- was written by
Claude (Anthropic's AI model), operating as an autonomous coding agent,
from the task PDF and no other input. That's a different situation
than "I used Copilot for boilerplate" or "I asked ChatGPT to debug
this one function," so it's worth being precise about what that means
rather than checking a box.

## What the AI did

Everything: reading and interpreting the brief, all architecture and
technology decisions (including the ones documented in the README as
deliberate tradeoffs -- e.g. cutting the YOLOv8n/torch local model for
a Tesseract-based one, running the pipeline synchronously, hand-rolling
screen navigation instead of React Navigation), the Django backend, the
matching engine and its test cases, the Expo frontend, `catalog.csv`
and its generator script, the synthetic test photos and the script
that makes them, the latency/cost benchmarking script and the numbers
it produced, this README, and the incremental commit history itself.

Past the initial build, the agent also drove the full setup and test
pass on a real machine directly -- installing dependencies, fixing
environment-specific breakage, adding a second hosted VLM provider,
running a real bookshelf photo through the live pipeline with a real
API key, and diagnosing what came back. That work is where most of the
genuinely interesting debugging happened, and it's covered in detail
below rather than summarized away.

## Debugging: what actually broke, and how it was found

Every item here was found by running the real thing -- executing code,
reading actual error output, inspecting actual API responses, viewing
actual image crops -- not by reading the code and reasoning about what
might go wrong. That distinction matters enough to spell out for each
one: what broke, how it was caught, and what the fix was.

**`run_pipeline()` silently overwriting a failed status with "done."**
The outer function set `status = "done"` unconditionally after the
inner pipeline function returned, even on a code path where the inner
function had already set `status = "failed"` for a corrupt-image case.
Found by a test asserting a corrupt image fails cleanly
(`test_corrupt_image_fails_cleanly`), not by inspection -- the bug only
shows up when you actually assert on the final state. Fixed by
introducing a `PipelineInputError` exception so failure classification
routes through one place instead of two competing status writes.

**A matching-cache staleness bug that only appeared running the full
suite, not file-by-file.** `catalog_store`'s in-process cache didn't
account for Django's test-transaction rollback between test classes.
Every file passed independently; running `pytest` on the whole suite
together failed. Found by literally doing both and comparing. Fixed
with an autouse fixture in `conftest.py` that clears the cache before
and after each test.

**A dependency-weight dead end, caught by attempting the install, not
by reading docs.** The original plan was a YOLOv8n/torch local
detector. `pip install torch` pulled several gigabytes of transitive
CUDA dependencies from PyPI's default Linux wheel; the CPU-only wheel
index wasn't reachable from the build network; disk had ~2.4GB free.
All three of those facts were established by actually trying the
install and reading what failed, not assumed ahead of time. That's
what motivated the Tesseract-based text-region detector instead -- a
real architectural decision driven by a real constraint, documented in
`spine_detector.py`'s module docstring.

**Image upload silently broken on web, working fine on a phone.**
`FormData.append(name, { uri, name, type })` is a React Native
polyfill shape that doesn't exist in a real browser -- on Expo web the
picker returns a `blob:`/`data:` URL that has to be `fetch()`ed back
into an actual `Blob` before appending. Found by actually running the
app in a browser and watching the upload silently send nothing, not by
reading the React Native docs. Fixed with a `Platform.OS === "web"`
branch in `frontend/src/api.js`.

**Zero detections on a real Mac despite identical code and identical
test images passing in the build environment.** Traced to `/tmp` being
a symlink to `/private/tmp` on macOS, which broke Tesseract/Leptonica's
temp-file reads in the `do shell script` execution context specifically
-- reproduced directly by running `tesseract` against the exact same
file at both paths and watching one work and one fail with a corrupted
filename in the Leptonica error. `spine_detector.py`'s broad
except-and-return-`[]` had been quietly converting this into "no
spines found," not an environment bug -- which is itself a finding:
graceful failure handling can hide a real bug as easily as a real
failure if you don't go looking. Fixed by pointing Python's `tempfile`
module at a real, non-symlinked directory inside the project.

**A 27.8MP real phone photo took ~13 seconds just for local detection,**
versus ~340ms on the small synthetic test photos -- found by uploading
an actual photo from the user's camera roll and timing it, not by
estimating from image size. Three full OCR rotation passes at full
phone resolution is a different order of magnitude of work than the
same passes on a test fixture. Fixed by downscaling to a 1600px cap for
the detection pass only, then rescaling the resulting boxes back up to
crop from the original full-resolution image for the VLM read --
confirmed 6.8x faster (13.1s -> 1.9s) on the identical photo, with the
existing 30-test suite still green.

**Two Python/Node version mismatches, both found by actually running
`pip install` / `npx expo start` and reading the real error, not
checking version numbers first.** Django 5.2 requires Python 3.10+; the
Mac had 3.9.6 (fixed via a Homebrew 3.12 venv). Expo SDK 57 requires
Node >=20.19.4; the Mac had 20.10.0, which failed with an opaque
`parseEnv is not a function` error rather than a clear version message
(fixed via `nvm install 22`).

**Gemini's free tier turned out to be a hard daily cap, not a per-minute
rate -- and the first fix for it was incomplete.** The first pass added
proactive throttling and retry-with-backoff on HTTP 429, on the
reasonable assumption that free tiers are rate-limited per minute. That
fix was real and correct, but running a 26-spine real photo through the
live pipeline afterward showed calls still failing -- reading the
actual 429 response body (not just the status code) showed Google
naming the exact quota: `GenerateRequestsPerDayPerProjectPerModel-
FreeTier`, value 20, for the specific model in use. No amount of
in-request backoff can wait out a daily cap. That led to testing
alternative models directly against the same live key
(`gemini-2.5-flash` turned out to be deprecated for new API keys,
returning 404) before landing on `gemini-3.1-flash-lite`, verified with
a real successful read before being made the default. Both the
throttle/backoff mechanism and the model choice are real fixes, found
in that order, for two different real problems.

**The review screen was hiding correct reads behind wrong catalog
guesses -- found by reading the actual database rows next to what the
screen displayed, not by assumption.** After the Gemini fixes, a real
photo of an academic/nonfiction shelf came back with the VLM correctly
reading titles like "The Decline and Fall of the Roman Empire" and "A
Farewell to Arms" -- genuinely accurate reads, confirmed by comparing
them against the photo. But the review screen showed completely
unrelated books instead ("A Game of Thrones," "Gone"). The cause,
found by reading `DetectedBookCard.js` line by line against the actual
`ScanSession` data: the card displayed `match_title` (the catalog's
best-effort guess, frequently wrong when the catalog has no real match
for that genre) ahead of `read_title` (what was actually printed on
the spine). The read was right the whole time; the UI was just
showing the wrong field. Fixed to show the actual read as primary,
with the catalog's guess demoted to a secondary line.

**A synthetic test photo with clean, legible bestseller titles still
only read 4 of 8 books correctly -- diagnosed by pulling the exact
image crops sent to the VLM, not by re-reading the source code.**
`photos/04_low_light_blur.jpg` looks crisp to a human at a glance. Four
of its eight spines nonetheless failed. Rather than guess why, the
actual cropped images the VLM received were pulled out of the
database and viewed directly: three were near-blank slivers of color
with no legible text in them at all, and the fourth (misread as "The
Great Gatsby") was similarly close to blank. The local detector was
splitting each spine's vertical text into separate fragments instead
of one clean region per spine, and for four of the eight, the fragment
that reached the VLM simply didn't contain the title. This confirmed
the detector-recall limitation already flagged in
`docs/latency_cost_notes.md` is real and reproducible, not a one-off,
and pinpointed exactly where in the pipeline it originates (detection,
not reading, not matching) with visual evidence rather than inference.

**A git history that quietly drifted from the actual file state, caught
before it became a broken submission.** Individual fixed files were
being copied onto the live machine as they were fixed, which is fast
but doesn't touch git -- the machine's `.git` history ended up six
commits behind the working files on disk, meaning a `git push` from
that machine would have published an incomplete, inconsistent history
even though the code itself was current. Caught by comparing `git log`
between the two copies, not assumed to be in sync. Fixed by treating
the fully-tested repository as the single source of truth and
resyncing history and working tree together, then confirming with
`git status` and a full test run on the live machine that nothing was
lost or silently reverted in the process (one real, previously
uncommitted local fix -- `react-dom`/`react-native-web`, needed for the
Expo web preview to run at all -- was caught this same way and folded
into a proper commit instead of being lost).

## What the AI did not do

Test on a real iOS or Android device or simulator -- the app has been
run and verified through Expo's web preview and a real backend on a
real Mac, with a real hosted VLM key and a real bookshelf photo, but
never through an actual mobile simulator or device. That's stated
plainly here rather than left to be discovered later, and it's called
out again in the README's "what's unfinished" section along with the
detector-recall limitation, which is real, measured, and not yet fully
solved -- see the debugging section above and `docs/latency_cost_notes.md`
for exactly what's still weak and why.
