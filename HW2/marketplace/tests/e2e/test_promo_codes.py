import uuid

import pytest

from tests.e2e.conftest import assert_error, money

VALID = {
    "discount_type": "FIXED_AMOUNT",
    "discount_value": 100,
    "max_uses": 10,
    "valid_from": "2026-01-01T00:00:00Z",
    "valid_until": "2027-01-01T00:00:00Z",
}


def code() -> str:
    return f"C{uuid.uuid4().hex[:12].upper()}"


def test_seller_creates_promo_code(api, db, seller):
    response = api.post("/promo-codes", seller.token, json={**VALID, "code": code()})
    assert response.status_code == 201
    promo = response.json()
    assert promo["current_uses"] == 0 and promo["active"] is True
    assert money(promo["min_order_amount"]) == 0
    row = db.one("SELECT code, discount_type, current_uses FROM promo_codes WHERE id = %s", promo["id"])
    assert row == {"code": promo["code"], "discount_type": "FIXED_AMOUNT", "current_uses": 0}


def test_user_cannot_create_promo_code(api, buyer):
    assert_error(api.post("/promo-codes", buyer.token, json={**VALID, "code": code()}), 403, "ACCESS_DENIED")


def test_duplicate_code(api, admin):
    value = code()
    assert api.post("/promo-codes", admin.token, json={**VALID, "code": value}).status_code == 201
    assert_error(api.post("/promo-codes", admin.token, json={**VALID, "code": value}), 409, "PROMO_CODE_ALREADY_EXISTS")


@pytest.mark.parametrize(
    "patch, field",
    [
        ({"code": "abc"}, "code"),
        ({"code": "SALE-10"}, "code"),
        ({"discount_value": 0}, "discount_value"),
        ({"max_uses": 0}, "max_uses"),
        ({"valid_until": "2025-01-01T00:00:00Z"}, "valid_until"),
        ({"valid_from": "2026-01-01T00:00:00"}, "valid_from"),
        ({"discount_type": "PERCENTAGE", "discount_value": 101}, "discount_value"),
    ],
)
def test_validation(api, admin, patch, field):
    body = assert_error(api.post("/promo-codes", admin.token, json={**VALID, "code": code(), **patch}), 400, "VALIDATION_ERROR")
    assert body["details"]["violations"][0]["field"] == field
