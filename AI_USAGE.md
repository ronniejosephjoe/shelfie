# AI Usage

Short and honest, per the brief's ask.

This entire repository -- every file, every commit -- was written by
Claude (Anthropic's AI model), operating as an autonomous coding agent
in a single session, from the task PDF and no other input. That's a
different situation than "I used Copilot for boilerplate" or "I asked
ChatGPT to debug this one function," so it's worth being precise about
what that means rather than checking a box.

## What the AI did

Everything: reading and interpreting the brief, all architecture and
technology decisions (including the ones documented in the README as
deliberate tradeoffs -- e.g. cutting the YOLOv8n/torch local model for
a Tesseract-based one, running the pipeline synchronously, hand-rolling
screen navigation instead of React Navigation), the Django backend, the
matching engine and its test cases, the Expo frontend, `catalog.csv`
and its generator script, the synthetic test photos and the script that
makes them, the latency/cost benchmarking script and the numbers it
produced, this README, and the incremental commit history itself.

Where the agent hit real engineering obstacles (documented candidly in
the commit history and README, not smoothed over): the intended
YOLOv8n/torch dependency chain turned out to require several gigabytes
of CUDA libraries and network access the build environment didn't have
-- the agent diagnosed this empirically (attempted the install, watched
it fail, checked disk space and reachable hosts) rather than assuming,
and changed approach as a result. Two real bugs were also found this
way, not by inspection: `run_pipeline()` silently overwriting a
"failed" status with "done" on one code path, and a matching-cache
staleness issue that only appeared when the full test suite ran
together rather than one file at a time. Both are described in the
commit messages for `scanner/tests/test_pipeline.py` and
`backend/conftest.py`, including how they were found and what the fix
was, not just that "tests were added."

## What the AI did not do

Verify this runs on a real phone, in a simulator, or against a real
photo of an actual bookshelf. The environment this was built in has no
camera, no iOS/Android simulator, and (per the README) no funded
OpenAI API key. Everything backend-side was validated for real
(Django's test client, the actual `pytest` suite, the matching engine
against the full real `catalog.csv`, real wall-clock timing of the
local model). The Expo app was validated by successfully bundling all
593 modules via `expo export`, not by running it. This is stated
plainly in the README's "what's unfinished" section rather than left
for someone to discover later.

## What this means for you (RJ)

You did not write this code, and the brief says explicitly: "assume
you will be asked to justify any line in the repository." That's a
real risk if you present this without having read it first. Before the
presentation, at minimum:

- Read `catalog/matching.py` and its tests -- this is the part the
  brief says will get checked most closely, and it's short enough to
  actually understand end to end.
- Read `scanner/services/pipeline.py` and the two services it calls --
  know what happens on a timeout, a malformed response, and a zero-
  detection scan, because that's explicitly one of the four things
  being evaluated.
- Actually run it (`pytest`, then the backend + Expo app together)
  before the deadline, not for the first time in front of an
  interviewer.
- Have an honest answer ready for "why did the AI make this choice"
  on the two or three decisions you find most surprising -- the
  README's "Key decisions" section is written to make that easy, but
  reading someone else's justification isn't the same as being able to
  defend it yourself under a follow-up question.
