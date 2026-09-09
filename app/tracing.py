"""Langfuse LLM tracing (Phase 5 — Observability).

Provides decorators and context managers for tracing LLM calls.
Captures prompt, completion, tokens, latency, and links to correlation ID.
"""

from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from app.config import settings
from app.middleware import get_correlation_id

# Langfuse client — lazy initialized
_langfuse = None


@lru_cache(maxsize=1)
def _get_langfuse():
    """Get or create the Langfuse client (singleton)."""
    global _langfuse
    if _langfuse is not None:
        return _langfuse

    if not settings.langfuse_secret_key or not settings.langfuse_public_key:
        return None

    try:
        from langfuse import Langfuse

        _langfuse = Langfuse(
            secret_key=settings.langfuse_secret_key,
            public_key=settings.langfuse_public_key,
            host=settings.langfuse_host,
        )
        return _langfuse
    except ImportError:
        return None
    except Exception:
        return None


def is_enabled() -> bool:
    """Check if Langfuse tracing is enabled and configured."""
    return _get_langfuse() is not None


@contextmanager
def trace_generation(
    name: str,
    model: str,
    input_text: str,
    metadata: dict[str, Any] | None = None,
):
    """Context manager for tracing an LLM generation.

    Usage:
        with trace_generation("answer", "claude-haiku", prompt) as trace:
            response = call_llm(prompt)
            trace.update(
                output=response.text,
                usage={"input": response.input_tokens, "output": response.output_tokens}
            )
    """
    langfuse = _get_langfuse()
    if langfuse is None:
        # Tracing disabled — yield a no-op context
        yield _NoOpTrace()
        return

    correlation_id = get_correlation_id()

    # Create a trace for this request
    trace = langfuse.trace(
        name=name,
        session_id=correlation_id or None,
        metadata=metadata or {},
    )

    # Create a generation span
    generation = trace.generation(
        name=f"{name}_generation",
        model=model,
        input=input_text,
        metadata=metadata or {},
    )

    try:
        yield _LangfuseGeneration(generation, trace)
    finally:
        # End the generation span
        generation.end()
        # Flush to ensure data is sent
        try:
            langfuse.flush()
        except Exception:
            pass


class _NoOpTrace:
    """No-op trace for when Langfuse is disabled."""

    def update(self, **kwargs):
        pass

    def score(self, **kwargs):
        pass


class _LangfuseGeneration:
    """Wrapper around Langfuse generation for easier updates."""

    def __init__(self, generation, trace):
        self._generation = generation
        self._trace = trace

    def update(
        self,
        output: str | None = None,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Update the generation with output and usage info."""
        update_kwargs = {}
        if output is not None:
            update_kwargs["output"] = output
        if usage is not None:
            update_kwargs["usage"] = usage
        if metadata is not None:
            update_kwargs["metadata"] = metadata

        if update_kwargs:
            self._generation.update(**update_kwargs)

    def score(self, name: str, value: float, comment: str | None = None):
        """Add a score to the trace (for evaluation)."""
        self._trace.score(name=name, value=value, comment=comment)
