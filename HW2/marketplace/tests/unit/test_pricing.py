from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.pricing import calculate_discount, promo_is_still_valid, promo_is_usable, subtotal
from generated.models import DiscountType

D = Decimal
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_subtotal():
    assert subtotal([(D("100.50"), 2), (D("25.00"), 1)]) == D("226.00")
    assert subtotal([]) == D("0.00")


@pytest.mark.parametrize(
    "amount, value, expected",
    [
        (D("400.00"), D("15"), D("60.00")),
        (D("33.33"), D("12.5"), D("4.17")),
        (D("100.00"), D("70"), D("70.00")),
        (D("100.00"), D("70.01"), None),
        (D("100.00"), D("100"), None),
    ],
)
def test_percentage(amount, value, expected):
    assert calculate_discount(amount, DiscountType.PERCENTAGE, value) == expected


@pytest.mark.parametrize(
    "amount, value, expected",
    [(D("100.00"), D("30"), D("30.00")), (D("50.00"), D("500"), D("50.00"))],
)
def test_fixed_amount(amount, value, expected):
    assert calculate_discount(amount, DiscountType.FIXED_AMOUNT, value) == expected


def promo(**overrides):
    return {
        "active": True,
        "current_uses": 0,
        "max_uses": 1,
        "valid_from": NOW - timedelta(days=1),
        "valid_until": NOW + timedelta(days=1),
        "now": NOW,
        **overrides,
    }


def test_promo_is_usable():
    assert promo_is_usable(**promo())
    assert promo_is_usable(**promo(valid_from=NOW, valid_until=NOW))
    assert not promo_is_usable(**promo(active=False))
    assert not promo_is_usable(**promo(current_uses=1))
    assert not promo_is_usable(**promo(valid_from=NOW + timedelta(seconds=1)))
    assert not promo_is_usable(**promo(valid_until=NOW - timedelta(seconds=1)))


def test_applied_promo_is_rechecked_without_uses():
    args = promo(current_uses=1)
    del args["current_uses"], args["max_uses"]
    assert promo_is_still_valid(**args)
    assert not promo_is_still_valid(**{**args, "active": False})
