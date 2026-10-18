import pytest

from app.domain.order_state import TRANSITIONS, can_transition, ensure_transition
from app.errors import ApiError
from generated.models import ErrorCode
from generated.models import OrderStatus as S

ALLOWED = {
    (S.CREATED, S.PAYMENT_PENDING),
    (S.CREATED, S.CANCELED),
    (S.PAYMENT_PENDING, S.PAID),
    (S.PAYMENT_PENDING, S.CANCELED),
    (S.PAID, S.SHIPPED),
    (S.SHIPPED, S.COMPLETED),
}


@pytest.mark.parametrize("current", list(S))
@pytest.mark.parametrize("target", list(S))
def test_transition_table(current, target):
    assert can_transition(current, target) == ((current, target) in ALLOWED)


def test_every_status_is_described():
    assert set(TRANSITIONS) == set(S)


def test_invalid_transition_error():
    with pytest.raises(ApiError) as error:
        ensure_transition(S.SHIPPED, S.CANCELED)
    assert error.value.code == ErrorCode.INVALID_STATE_TRANSITION
    assert error.value.status_code == 409
    assert error.value.details == {"current_status": "SHIPPED", "target_status": "CANCELED"}
