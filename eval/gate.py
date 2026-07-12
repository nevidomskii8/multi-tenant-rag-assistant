"""Deterministic retrieval gate — the CI-safe eval (no LLM, no API key).

For every in-scope golden question, check that the expected source document is
present in the top-k retrieved chunks. This catches the common silent
regressions — broken ingest, wrong embedding model/dim, chunking or retriever
bugs — without calling Claude, so it is cheap, reproducible, and never flaky.

    DATABASE_URL=postgresql://app:<pw>@localhost:5432/app python -m eval.gate

Exits non-zero if hit-rate falls below the threshold, failing the CI build.
"""

import json
import sys
from pathlib import Path

from app.retrieval import retrieve

GOLDEN = Path(__file__).parent / "golden.json"
# With only a handful of chunks, k=4 returns every document, so "expected in
# top-k" is trivially true. We assert the stricter, meaningful thing: the
# expected document ranks FIRST. Raise K to "in top-k" once the KB grows.
K = 1
THRESHOLD = 1.0


def main() -> None:
    items = [x for x in json.loads(GOLDEN.read_text()) if x.get("expected_source")]
    hits = 0
    for item in items:
        sources = {h["source"] for h in retrieve(item["question"], k=K)}
        ok = item["expected_source"] in sources
        hits += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {item['id']}: {item['expected_source']}")

    rate = hits / len(items) if items else 0.0
    print(f"\nRetrieval hit-rate: {hits}/{len(items)} = {rate:.0%} (threshold {THRESHOLD:.0%})")
    if rate < THRESHOLD:
        print("GATE FAILED", file=sys.stderr)
        sys.exit(1)
    print("GATE PASSED")


if __name__ == "__main__":
    main()
