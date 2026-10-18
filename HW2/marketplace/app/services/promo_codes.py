from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError, validation_error
from app.orm import PromoCode
from generated.apis import PromoCodesApi
from generated.models import (
    DiscountType,
    ErrorCode,
    FieldViolation,
    PromoCodeCreate,
    PromoCodeResponse,
    ViolationLocation,
)
from generated.security import Principal


class PromoCodeService(PromoCodesApi):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_promo_code(self, *, principal: Principal, body: PromoCodeCreate) -> PromoCodeResponse:
        self._validate(body)
        promo = PromoCode(
            code=body.code,
            discount_type=body.discount_type,
            discount_value=body.discount_value,
            min_order_amount=Decimal(body.min_order_amount),
            max_uses=body.max_uses,
            current_uses=0,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            active=body.active,
        )
        try:
            async with self.session.begin():
                self.session.add(promo)
        except IntegrityError:
            raise ApiError(
                ErrorCode.PROMO_CODE_ALREADY_EXISTS, "Promo code with this code already exists", {"code": body.code}
            ) from None
        return PromoCodeResponse.model_validate(promo, from_attributes=True)

    @staticmethod
    def _validate(body: PromoCodeCreate) -> None:
        violations = []
        if body.valid_until <= body.valid_from:
            violations.append(
                FieldViolation(
                    field="valid_until",
                    location=ViolationLocation.body,
                    code="date_range",
                    message="valid_until must be later than valid_from",
                )
            )
        if body.discount_type == DiscountType.PERCENTAGE and body.discount_value > Decimal(100):
            violations.append(
                FieldViolation(
                    field="discount_value",
                    location=ViolationLocation.body,
                    code="less_than_equal",
                    message="PERCENTAGE discount_value must be less than or equal to 100",
                )
            )
        if violations:
            raise validation_error(violations)

