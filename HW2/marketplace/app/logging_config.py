import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

from app.context import current_request_id

_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName", "color_message"}


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": utc_iso(datetime.fromtimestamp(record.created, UTC)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        return line.encode("utf-8", "backslashreplace").decode("utf-8")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            request_id = current_request_id()
            if request_id is not None:
                record.request_id = request_id
        return True


def logging_config(level: str = "INFO") -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json",
                "filters": ["request_id"],
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
        },
    }


def configure_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(logging_config(level))
    logging.captureWarnings(True)
