import asyncio
import json
import logging

from app.errors import HTTP_STATUS, to_violation
from app.logging_config import JsonFormatter
from app.middleware import RequestContextMiddleware, body_for_log, mask_sensitive
from generated.models import ErrorCode


def test_every_error_code_has_http_status():
    assert set(HTTP_STATUS) == set(ErrorCode)


def test_status_codes_from_the_task():
    expected = {
        "PRODUCT_NOT_FOUND": 404, "PRODUCT_INACTIVE": 409, "ORDER_NOT_FOUND": 404, "ORDER_LIMIT_EXCEEDED": 429,
        "ORDER_HAS_ACTIVE": 409, "INVALID_STATE_TRANSITION": 409, "INSUFFICIENT_STOCK": 409,
        "PROMO_CODE_INVALID": 422, "PROMO_CODE_MIN_AMOUNT": 422, "ORDER_OWNERSHIP_VIOLATION": 403,
        "VALIDATION_ERROR": 400, "TOKEN_EXPIRED": 401, "TOKEN_INVALID": 401, "REFRESH_TOKEN_INVALID": 401,
        "ACCESS_DENIED": 403,
    }
    assert {code: HTTP_STATUS[ErrorCode(code)] for code in expected} == expected


def test_violation_paths():
    v = to_violation({"loc": ("body", "items", 0, "quantity"), "type": "less_than_equal", "msg": "too big"})
    assert (v.field, v.location.value, v.code, v.message) == ("items[0].quantity", "body", "less_than_equal", "too big")
    assert to_violation({"loc": ("query", "size"), "type": "x", "msg": "m"}).field == "size"
    assert to_violation({"loc": ("body",), "type": "missing", "msg": "m"}).field == "body"
    assert to_violation({"loc": ("body", 12), "type": "json_invalid", "msg": "m"}).field == "body"


def test_sensitive_fields_are_masked():
    body = {"email": "a@b.c", "password": "secret", "nested": [{"refresh_token": "t", "x": 1}], "client_secret": "s"}
    assert mask_sensitive(body) == {
        "email": "a@b.c", "password": "***", "nested": [{"refresh_token": "***", "x": 1}], "client_secret": "***"
    }


def test_body_for_log():
    assert body_for_log(b"") is None
    assert body_for_log(b'{"password": "p"}') == {"password": "***"}
    assert body_for_log(b"password=p") == "<10 bytes of non-JSON body>"
    assert body_for_log(b'{"price": NaN}') == "<14 bytes of non-JSON body>"
    assert body_for_log(b"[" * 5000 + b"]" * 5000).endswith("non-JSON body>")
    assert body_for_log(b"x" * 20000) == "<20000 bytes, not logged>"


def test_body_is_logged_even_if_the_handler_ignores_it(caplog):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    messages = iter([{"type": "http.request", "body": b'{"password": "p", "x": 1}', "more_body": False}])
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/orders/1/cancel", "headers": [], "query_string": b""}
    with caplog.at_level(logging.INFO, logger="marketplace.access"):
        asyncio.run(RequestContextMiddleware(app)(scope, receive, send))

    entry = caplog.records[-1]
    assert entry.request_body == {"password": "***", "x": 1}
    assert entry.status_code == 200 and entry.endpoint == "/orders/1/cancel"
    assert (b"x-request-id", entry.request_id.encode()) in sent[0]["headers"]


def test_log_line_is_always_valid_utf8_json():
    record = logging.makeLogRecord({"msg": "POST /products -> 400", "request_body": {"name": "\ud800 Кириллица"}})
    line = JsonFormatter().format(record)
    line.encode("utf-8")
    assert json.loads(line)["request_body"]["name"] == "\ud800 Кириллица"
