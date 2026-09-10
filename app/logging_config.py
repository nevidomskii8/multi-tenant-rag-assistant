"""Structured logging configuration (Phase 5 — Observability).

Uses structlog for JSON-formatted logs with correlation IDs.
Logs go to stdout; Promtail/Alloy collects and ships to Loki.

NOTE: We intentionally do NOT use python-logging-loki's LokiHandler here.
That handler does synchronous HTTP POST on every log line — if Loki is slow
or down, it blocks the request path. The idiomatic Docker/K8s pattern is:
  app → stdout → Promtail/Alloy → Loki
This decouples observability from request latency.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from app.config import settings


def _add_correlation_id(
    logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Add correlation ID from context var to every log entry."""
    from app.middleware import get_correlation_id

    corr_id = get_correlation_id()
    if corr_id:
        event_dict["correlation_id"] = corr_id
    return event_dict


def _add_app_context(
    logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Add static app context fields."""
    event_dict["service"] = "multi-tenant-rag"
    event_dict["env"] = settings.app_env
    return event_dict


def configure_logging() -> None:
    """Configure structlog and standard logging for the app."""

    # Shared processors for structlog
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_correlation_id,
        _add_app_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    formatter = structlog.stdlib.ProcessorFormatter(
        # JSON output for production, console for dev
        processor=(
            structlog.processors.JSONRenderer()
            if settings.app_env != "dev"
            else structlog.dev.ConsoleRenderer(colors=True)
        ),
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger for the given module name."""
    return structlog.get_logger(name)
