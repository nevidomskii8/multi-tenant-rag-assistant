# Backlog

Deferred items — not part of any active phase's DoD. Pull one only when its
phase reaches it, or promote to a GitHub issue labeled `backlog`.

## From Phase 1

- **Minimal chat UI** (was Phase 1 step 5): one static HTML page over `POST /chat`
  — input box, render answer + sources. Cosmetic/demo; not required by DoD.
- **DeepEval integration**: add the DeepEval framework as an alternative metric
  runner over the existing `eval/golden.json` (portfolio value; recognised tool).
  Golden set is already framework-neutral, so this is additive.
- **HF model cache volume in docker-compose**: mount a named volume for
  `~/.cache/huggingface` (+ `HF_HUB_OFFLINE=1`) on the `app` service so the
  container does not re-download the ~450MB embedding model on every start.
- **Smarter chunker**: current `chunk_text` is token-window based. Consider
  sentence/heading-aware splitting for better semantic boundaries.
- **httpx/starlette deprecation**: pytest warns `Using httpx with
  starlette.testclient is deprecated; install httpx2`. Non-blocking; resolve
  when convenient.

## Eval hardening (deferred, low priority)

- **Judge-based refusal check**: `eval/run.py` detects refusals with a keyword
  list (`REFUSAL_MARKERS`) coupled to rag.py's system-prompt wording. Sturdier
  to ask the LLM judge "did this appropriately decline?" instead.
- **Separate judge model**: the judge reuses `settings.claude_model` — same
  model as the generator (self-preference bias). Add a distinct `judge_model`
  setting (a different/stronger model) if judge scores drift from manual review.