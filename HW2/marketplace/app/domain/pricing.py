from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from generated.models import DiscountType

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_AMOUNT = Decimal("9999999999.99")
MAX_PERCENTAGE_DISCOUNT = Decimal(70)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def subtotal(lines: Iterable[tuple[Decimal, int]]) -> Decimal:
    return money(sum((price * quantity for price, quantity in lines), ZERO))


def promo_is_usable(*, active: bool, current_uses: int, max_uses: int, valid_from: datetime,
                    valid_until: datetime, now: datetime) -> bool:
    return active and current_uses < max_uses and valid_from <= now <= valid_until


def promo_is_still_valid(*, active: bool, valid_from: datetime, valid_until: datetime, now: datetime) -> bool:
    return active and valid_from <= now <= valid_until


def calculate_discount(amount: Decimal, discount_type: DiscountType, discount_value: Decimal) -> Decimal | None:
    if discount_type == DiscountType.PERCENTAGE:
        if discount_value > MAX_PERCENTAGE_DISCOUNT:
            return None
        return money(amount * discount_value / Decimal(100))
    return money(min(discount_value, amount))
