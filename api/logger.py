import structlog
import logging
import sys
from contextvars import ContextVar
from config import settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="no-request-id")

def configure_logging():
    def add_request_id(logger, method_name, event_dict):
        event_dict["request_id"] = request_id_ctx.get()
        return event_dict

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_request_id,
        structlog.processors.JSONRenderer()
    ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )

def get_logger(name: str):
    return structlog.get_logger(name)