from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID


@dataclass
class RequestContext:
    request_id: str
    user_id: UUID | None = None


request_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def current_request_id() -> str | None:
    ctx = request_context.get()
    return ctx.request_id if ctx else None


def bind_user(user_id: UUID) -> None:
    ctx = request_context.get()
    if ctx is not None:
        ctx.user_id = user_id
