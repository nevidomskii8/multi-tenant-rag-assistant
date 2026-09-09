"""Prometheus metrics definitions (Phase 5 — Observability).

All metrics are defined here and imported by the modules that update them.
The /metrics endpoint is exposed via prometheus_client's make_asgi_app().
"""

from prometheus_client import Counter, Histogram

# HTTP request metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# LLM (Claude API) metrics
llm_requests_total = Counter(
    "llm_requests_total",
    "Total LLM API requests",
    ["model", "room_id"],
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "Total tokens consumed",
    ["model", "direction"],  # direction: input | output
)

llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "LLM request latency in seconds",
    ["model"],
    buckets=(0.5, 1, 2.5, 5, 10, 15, 20, 30, 45, 60),
)

# Cost tracking (LLM10 denial-of-wallet defense)
# Prices per 1M tokens (as of 2024, subject to change):
# Haiku: $0.25 input, $1.25 output
# Sonnet: $3 input, $15 output
# Opus: $15 input, $75 output
llm_cost_dollars_total = Counter(
    "llm_cost_dollars_total",
    "Estimated LLM API cost in USD",
    ["model"],
)

# RAG retrieval metrics
retrieval_requests_total = Counter(
    "retrieval_requests_total",
    "Total retrieval requests",
    ["room_id"],
)

retrieval_chunks_returned = Histogram(
    "retrieval_chunks_returned",
    "Number of chunks returned per retrieval",
    ["room_id"],
    buckets=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
)

retrieval_empty_total = Counter(
    "retrieval_empty_total",
    "Retrieval requests returning zero chunks",
    ["room_id"],
)

retrieval_duration_seconds = Histogram(
    "retrieval_duration_seconds",
    "Retrieval latency in seconds",
    [],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)

# Guardrail metrics
guardrail_verdicts_total = Counter(
    "guardrail_verdicts_total",
    "Guardrail scan verdicts",
    ["scanner", "verdict"],  # verdict: allowed | flagged | blocked
)

guardrail_duration_seconds = Histogram(
    "guardrail_duration_seconds",
    "Guardrail scan latency in seconds",
    ["stage"],  # stage: input | output
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

# Rate limiting metrics (LLM10)
rate_limit_hits_total = Counter(
    "rate_limit_hits_total",
    "Rate limit hits",
    ["user_id", "endpoint"],
)


# Claude API pricing (per 1M tokens, as of 2024)
_PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-3-5-haiku-latest": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 15.00, "output": 75.00},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a single LLM call."""
    prices = _PRICING.get(model, {"input": 3.00, "output": 15.00})  # default to Sonnet
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000
