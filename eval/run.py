"""Full RAG eval — run locally / by hand (needs ANTHROPIC_API_KEY).

Generates an answer for every golden question, then scores it. Deliberately NOT
wired into CI: it calls Claude (costs money, mildly non-deterministic). Run it
after changing the prompt, retriever, or KB:

    DATABASE_URL=postgresql://app:<pw>@localhost:5432/app python -m eval.run

Metrics:
- fact check (deterministic): every `must_include` string appears in the answer.
- refusal (deterministic): out-of-scope questions are declined, not answered.
- faithfulness / relevancy (LLM judge): Claude grades each in-scope answer.
"""

import json
import re
import sys
from pathlib import Path

from anthropic import Anthropic

from app.config import settings
from app.rag import answer

GOLDEN = Path(__file__).parent / "golden.json"
FAITHFULNESS_MIN = 0.8
RELEVANCY_MIN = 0.8

# Keyword heuristic, coupled by convention to rag.py's system prompt ("say you
# don't know"). If that wording changes this list silently goes stale — a
# judge-based refusal check would be sturdier (see docs/backlog.md).
REFUSAL_MARKERS = (
    "don't know",
    "do not know",
    "no information",
    "not contain",
    "isn't in",
    "not in the",
    "cannot find",
    "couldn't find",
)

JUDGE_SYSTEM = (
    "You are a strict evaluator of a retrieval-augmented answer. Score two things "
    "from 0.0 to 1.0:\n"
    "- faithfulness: is every claim in the answer supported by the context? "
    "(1.0 = fully grounded, 0.0 = fabricated)\n"
    "- relevancy: does the answer actually address the question?\n"
    'Reply with ONLY a JSON object: {"faithfulness": <float>, "relevancy": '
    '<float>, "reason": "<short>"}'
)


# NOTE: the judge uses the SAME model as the generator (settings.claude_model).
# Known LLM-as-judge limitation (self-preference bias). If judge scores diverge
# from manual review, a separate/stronger judge model is the first thing to try
# (see docs/backlog.md).
def _judge_client() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def _parse_json(text: str) -> dict:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    # Tolerate a preamble ("Here's my evaluation: {...}") by extracting the first
    # JSON object instead of assuming the whole reply is clean JSON.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in judge output: {text[:80]!r}")
    return json.loads(match.group(0))


def judge(client: Anthropic, question: str, context: str, ans: str) -> dict:
    resp = client.messages.create(
        model=settings.claude_model,
        max_tokens=300,
        system=JUDGE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"<context>\n{context}\n</context>\n\nQuestion: {question}\n\nAnswer: {ans}"
                ),
            }
        ],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(text)


def main() -> None:
    items = json.loads(GOLDEN.read_text())
    client = _judge_client()
    faith_scores: list[float] = []
    rel_scores: list[float] = []
    failures: list[str] = []

    for item in items:
        result = answer(item["question"])
        ans = result["answer"]

        if item.get("expect_refusal"):
            refused = any(m in ans.lower() for m in REFUSAL_MARKERS)
            print(f"  [{'PASS' if refused else 'FAIL'}] {item['id']}: refusal")
            if not refused:
                failures.append(f"{item['id']}: expected a refusal, got an answer")
            continue

        missing = [s for s in item.get("must_include", []) if s.lower() not in ans.lower()]
        fact_ok = not missing
        if not fact_ok:
            failures.append(f"{item['id']}: answer missing facts {missing}")

        # Judge against the EXACT context the model saw (returned by answer()),
        # not a re-retrieval that could drift if k ever differs.
        try:
            verdict = judge(client, item["question"], result["context"], ans)
            faith_scores.append(verdict["faithfulness"])
            rel_scores.append(verdict["relevancy"])
        except Exception as exc:
            # One bad judge reply must not abort the whole run — record and move on.
            failures.append(f"{item['id']}: judge error: {exc}")
            print(f"  [ERROR] {item['id']}: judge failed ({exc})")
            continue
        print(
            f"  [{'PASS' if fact_ok else 'FAIL'}] {item['id']}: "
            f"faithfulness={verdict['faithfulness']:.2f} "
            f"relevancy={verdict['relevancy']:.2f} facts={'ok' if fact_ok else missing}"
        )

    faith_avg = sum(faith_scores) / len(faith_scores) if faith_scores else 0.0
    rel_avg = sum(rel_scores) / len(rel_scores) if rel_scores else 0.0
    print(
        f"\nfaithfulness avg {faith_avg:.2f} (min {FAITHFULNESS_MIN}) | "
        f"relevancy avg {rel_avg:.2f} (min {RELEVANCY_MIN})"
    )
    if faith_avg < FAITHFULNESS_MIN:
        failures.append(f"faithfulness {faith_avg:.2f} < {FAITHFULNESS_MIN}")
    if rel_avg < RELEVANCY_MIN:
        failures.append(f"relevancy {rel_avg:.2f} < {RELEVANCY_MIN}")

    if failures:
        print("\nEVAL FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)
    print("EVAL PASSED")


if __name__ == "__main__":
    main()
