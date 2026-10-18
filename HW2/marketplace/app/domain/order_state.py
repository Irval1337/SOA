from app.errors import ApiError
from generated.models import ErrorCode, OrderStatus

TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.PAYMENT_PENDING, OrderStatus.CANCELED}),
    OrderStatus.PAYMENT_PENDING: frozenset({OrderStatus.PAID, OrderStatus.CANCELED}),
    OrderStatus.PAID: frozenset({OrderStatus.SHIPPED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.COMPLETED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
}

ACTIVE_STATUSES = frozenset({OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING})
EDITABLE_STATUSES = frozenset({OrderStatus.CREATED})


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in TRANSITIONS[current]


def ensure_transition(current: OrderStatus, target: OrderStatus) -> None:
    if not can_transition(current, target):
        raise ApiError(
            ErrorCode.INVALID_STATE_TRANSITION,
            f"Cannot change order status from {current.value} to {target.value}",
            {"current_status": current.value, "target_status": target.value},
        )
