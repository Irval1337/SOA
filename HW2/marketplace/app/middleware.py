import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.context import RequestContext, request_context
from app.errors import error_response
from app.logging_config import utc_iso
from generated.models import ErrorCode

access_logger = logging.getLogger("marketplace.access")
error_logger = logging.getLogger("marketplace.error")

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_KEYS = ("password", "token", "secret")
MAX_LOGGED_BODY_BYTES = 16 * 1024
MASK = "***"


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: MASK if any(s in str(key).lower() for s in SENSITIVE_KEYS) else mask_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    return value


def _reject_constant(name: str) -> None:
    raise ValueError(f"non-standard JSON constant {name}")


def body_for_log(raw: bytes) -> Any:
    if not raw:
        return None
    if len(raw) > MAX_LOGGED_BODY_BYTES:
        return f"<{len(raw)} bytes, not logged>"
    try:
        return mask_sensitive(json.loads(raw, parse_constant=_reject_constant))
    except (ValueError, RecursionError):
        return f"<{len(raw)} bytes of non-JSON body>"


async def _buffer_body(receive: Receive) -> tuple[bytes, Receive]:
    messages: list[Message] = []
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request" or not message.get("more_body", False):
            break
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.request")

    async def replay() -> Message:
        return messages.pop(0) if messages else await receive()

    return body, replay


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        ctx = RequestContext(request_id=str(uuid.uuid4()))
        token = request_context.set(ctx)
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        method: str = scope["method"]
        capture_body = method in MUTATING_METHODS
        body = b""
        if capture_body:
            body, receive = await _buffer_body(receive)
        state = {"status": 500, "response_started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["response_started"] = True
                MutableHeaders(scope=message)["X-Request-Id"] = ctx.request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            error_logger.exception("Unhandled exception while processing request")
            if not state["response_started"]:
                response = error_response(ErrorCode.INTERNAL_ERROR, "Internal server error")
                await response(scope, receive, send_wrapper)
            state["status"] = 500
        finally:
            entry: dict[str, Any] = {
                "request_id": ctx.request_id,
                "method": method,
                "endpoint": scope["path"],
                "status_code": state["status"],
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "user_id": str(ctx.user_id) if ctx.user_id else None,
                "timestamp": utc_iso(started_at),
            }
            if capture_body:
                entry["request_body"] = body_for_log(body)
            access_logger.info("%s %s -> %s", method, scope["path"], state["status"], extra=entry)
            request_context.reset(token)
