import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.domain.order_state import ACTIVE_STATUSES, EDITABLE_STATUSES, ensure_transition
from app.domain.pricing import (
    MAX_AMOUNT,
    ZERO,
    calculate_discount,
    promo_is_still_valid,
    promo_is_usable,
    subtotal,
)
from app.errors import ApiError, validation_error
from app.orm import OperationType, Order, OrderItem, Product, PromoCode, User, UserOperation
from generated.apis import OrdersApi
from generated.models import (
    ErrorCode,
    FieldViolation,
    InsufficientStockDetails,
    OrderCreate,
    OrderItemRequest,
    OrderItemResponse,
    OrderResponse,
    OrderStatus,
    OrderStatusChange,
    OrderUpdate,
    ProductStatus,
    StockShortage,
    UserRole,
    ViolationLocation,
)
from generated.security import Principal


def to_order_response(order: Order, promo_code: str | None) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        status=order.status,
        items=[
            OrderItemResponse(product_id=item.product_id, quantity=item.quantity, price_at_order=item.price_at_order)
            for item in order.items
        ],
        promo_code=promo_code,
        subtotal_amount=order.total_amount + order.discount_amount,
        discount_amount=order.discount_amount,
        total_amount=order.total_amount,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


class OrderService(OrdersApi):
    def __init__(self, session: AsyncSession, rate_limit: timedelta) -> None:
        self.session = session
        self.rate_limit = rate_limit

    async def get_order(self, *, principal: Principal, id: UUID) -> OrderResponse:
        async with self.session.begin():
            order = await self._load_order(id, lock="share")
            self._check_owner(order, principal)
            promo_code = await self._promo_code(order.promo_code_id)
        return to_order_response(order, promo_code)

    async def create_order(self, *, principal: Principal, body: OrderCreate) -> OrderResponse:
        requested = self._requested_items(body.items)
        async with self.session.begin():
            await self._lock_user(principal.user_id)
            await self._check_rate_limit(principal.user_id, OperationType.CREATE_ORDER)
            await self._check_no_active_order(principal.user_id)
            products = await self._lock_products(requested)
            self._check_catalog(requested, products)
            self._check_stock(requested, products)
            order = Order(user_id=principal.user_id, status=OrderStatus.CREATED)
            order.items = self._reserve(requested, products)
            amount = self._amount(order.items)
            discount = ZERO
            promo: PromoCode | None = None
            if body.promo_code is not None:
                promo = await self._lock_promo_by_code(body.promo_code)
                applied = self._apply_promo(promo, body.promo_code, amount)
                if applied is not None:
                    discount = applied
                    promo.current_uses += 1
                    order.promo_code_id = promo.id
            order.discount_amount = discount
            order.total_amount = amount - discount
            self.session.add(order)
            self.session.add(UserOperation(user_id=principal.user_id, operation_type=OperationType.CREATE_ORDER))
            try:
                await self.session.flush()
            except IntegrityError as exc:
                if "ux_orders_one_active_per_user" in str(exc.orig):
                    raise ApiError(ErrorCode.ORDER_HAS_ACTIVE, "User already has an active order") from None
                raise
        return to_order_response(order, promo.code if order.promo_code_id else None)

    async def update_order(self, *, principal: Principal, id: UUID, body: OrderUpdate) -> OrderResponse:
        requested = self._requested_items(body.items)
        async with self.session.begin():
            await self._lock_user(principal.user_id)
            order = await self._load_order(id, lock="update")
            self._check_owner(order, principal)
            if order.status not in EDITABLE_STATUSES:
                raise ApiError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"Order can be updated only in CREATED status, current status is {order.status.value}",
                    {"current_status": order.status.value, "allowed_statuses": [s.value for s in EDITABLE_STATUSES]},
                )
            await self._check_rate_limit(principal.user_id, OperationType.UPDATE_ORDER)
            products = await self._lock_products({item.product_id for item in order.items} | requested.keys())
            for item in order.items:
                products[item.product_id].stock += item.quantity
            self._check_catalog(requested, products)
            self._check_stock(requested, products)
            order.items.clear()
            await self.session.flush()
            order.items.extend(self._reserve(requested, products))
            amount = self._amount(order.items)
            discount = ZERO
            promo_code: str | None = None
            if order.promo_code_id is not None:
                promo = await self.session.get(PromoCode, order.promo_code_id, with_for_update=True)
                reapplied = self._reapply_promo(promo, amount)
                if reapplied is None:
                    promo.current_uses -= 1
                    order.promo_code_id = None
                else:
                    discount = reapplied
                    promo_code = promo.code
            order.discount_amount = discount
            order.total_amount = amount - discount
            flag_modified(order, "total_amount")
            self.session.add(UserOperation(user_id=principal.user_id, operation_type=OperationType.UPDATE_ORDER))
        return to_order_response(order, promo_code)

    async def cancel_order(self, *, principal: Principal, id: UUID) -> OrderResponse:
        async with self.session.begin():
            order = await self._load_order(id, lock="update")
            self._check_owner(order, principal)
            promo_code = await self._cancel(order)
        return to_order_response(order, promo_code)

    async def change_order_status(
        self, *, principal: Principal, id: UUID, body: OrderStatusChange
    ) -> OrderResponse:
        async with self.session.begin():
            order = await self._load_order(id, lock="update")
            if body.status == OrderStatus.CANCELED:
                promo_code = await self._cancel(order)
            else:
                ensure_transition(order.status, body.status)
                order.status = body.status
                promo_code = await self._promo_code(order.promo_code_id)
        return to_order_response(order, promo_code)

    async def _cancel(self, order: Order) -> str | None:
        ensure_transition(order.status, OrderStatus.CANCELED)
        products = await self._lock_products({item.product_id for item in order.items})
        for item in order.items:
            products[item.product_id].stock += item.quantity
        promo_code = None
        if order.promo_code_id is not None:
            promo = await self.session.get(PromoCode, order.promo_code_id, with_for_update=True)
            promo.current_uses -= 1
            promo_code = promo.code
        order.status = OrderStatus.CANCELED
        return promo_code

    @staticmethod
    def _requested_items(items: Sequence[OrderItemRequest]) -> dict[UUID, int]:
        requested: dict[UUID, int] = {}
        duplicates = []
        for index, item in enumerate(items):
            if item.product_id in requested:
                duplicates.append(
                    FieldViolation(
                        field=f"items[{index}].product_id",
                        location=ViolationLocation.body,
                        code="duplicate",
                        message="Each product_id may appear in items only once",
                    )
                )
            requested[item.product_id] = item.quantity
        if duplicates:
            raise validation_error(duplicates)
        return requested

    async def _lock_user(self, user_id: UUID) -> None:
        if await self.session.scalar(select(User.id).where(User.id == user_id).with_for_update()) is None:
            raise ApiError(ErrorCode.TOKEN_INVALID, "User of the access token does not exist")

    async def _check_rate_limit(self, user_id: UUID, operation: OperationType) -> None:
        if self.rate_limit <= timedelta(0):
            return
        row = (
            await self.session.execute(
                select(UserOperation.created_at, func.now())
                .where(UserOperation.user_id == user_id, UserOperation.operation_type == operation)
                .order_by(UserOperation.created_at.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return
        last_at, db_now = row
        elapsed = db_now - last_at
        if elapsed < self.rate_limit:
            retry_after = max(1, math.ceil((self.rate_limit - elapsed).total_seconds()))
            raise ApiError(
                ErrorCode.ORDER_LIMIT_EXCEEDED,
                f"Too many {operation.value} operations, retry in {retry_after} s",
                {"operation_type": operation.value, "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

    async def _check_no_active_order(self, user_id: UUID) -> None:
        active = (
            await self.session.execute(
                select(Order.id, Order.status)
                .where(Order.user_id == user_id, Order.status.in_(ACTIVE_STATUSES))
                .limit(1)
            )
        ).first()
        if active is not None:
            raise ApiError(
                ErrorCode.ORDER_HAS_ACTIVE,
                "User already has an active order",
                {"order_id": str(active.id), "status": active.status.value},
            )

    async def _lock_products(self, product_ids: Iterable[UUID]) -> dict[UUID, Product]:
        ids = sorted(set(product_ids))
        if not ids:
            return {}
        rows = await self.session.scalars(
            select(Product).where(Product.id.in_(ids)).order_by(Product.id).with_for_update()
        )
        return {product.id: product for product in rows}

    @staticmethod
    def _check_catalog(requested: dict[UUID, int], products: dict[UUID, Product]) -> None:
        missing = [str(pid) for pid in requested if pid not in products]
        if missing:
            raise ApiError(ErrorCode.PRODUCT_NOT_FOUND, "Product not found", {"product_ids": missing})
        inactive = [str(pid) for pid in requested if products[pid].status != ProductStatus.ACTIVE]
        if inactive:
            raise ApiError(
                ErrorCode.PRODUCT_INACTIVE, "Some products are not available for ordering", {"product_ids": inactive}
            )

    @staticmethod
    def _check_stock(requested: dict[UUID, int], products: dict[UUID, Product]) -> None:
        shortages = [
            StockShortage(product_id=pid, requested=quantity, available=products[pid].stock)
            for pid, quantity in requested.items()
            if products[pid].stock < quantity
        ]
        if shortages:
            raise ApiError(
                ErrorCode.INSUFFICIENT_STOCK,
                "Insufficient stock for some products",
                InsufficientStockDetails(items=shortages),
            )

    @staticmethod
    def _reserve(requested: dict[UUID, int], products: dict[UUID, Product]) -> list[OrderItem]:
        items = []
        for product_id in sorted(requested):
            product = products[product_id]
            product.stock -= requested[product_id]
            items.append(
                OrderItem(product_id=product_id, quantity=requested[product_id], price_at_order=product.price)
            )
        return items

    @staticmethod
    def _amount(items: Iterable[OrderItem]) -> Decimal:
        amount = subtotal((item.price_at_order, item.quantity) for item in items)
        if amount > MAX_AMOUNT:
            raise validation_error(
                [
                    FieldViolation(
                        field="items",
                        location=ViolationLocation.body,
                        code="amount_overflow",
                        message=f"Order amount must not exceed {MAX_AMOUNT}",
                    )
                ]
            )
        return amount

    async def _lock_promo_by_code(self, code: str) -> PromoCode | None:
        return await self.session.scalar(select(PromoCode).where(PromoCode.code == code).with_for_update())

    @staticmethod
    def _apply_promo(promo: PromoCode | None, code: str, amount: Decimal) -> Decimal | None:
        now = datetime.now(UTC)
        if promo is None or not promo_is_usable(
            active=promo.active,
            current_uses=promo.current_uses,
            max_uses=promo.max_uses,
            valid_from=promo.valid_from,
            valid_until=promo.valid_until,
            now=now,
        ):
            raise ApiError(
                ErrorCode.PROMO_CODE_INVALID,
                "Promo code is not found, inactive, expired or exhausted",
                {"promo_code": code},
            )
        if amount < promo.min_order_amount:
            raise ApiError(
                ErrorCode.PROMO_CODE_MIN_AMOUNT,
                "Order amount is below the promo code minimum",
                {"promo_code": code, "min_order_amount": promo.min_order_amount, "order_amount": amount},
            )
        return calculate_discount(amount, promo.discount_type, promo.discount_value)

    @staticmethod
    def _reapply_promo(promo: PromoCode, amount: Decimal) -> Decimal | None:
        if not promo_is_still_valid(
            active=promo.active, valid_from=promo.valid_from, valid_until=promo.valid_until, now=datetime.now(UTC)
        ):
            return None
        if amount < promo.min_order_amount:
            return None
        return calculate_discount(amount, promo.discount_type, promo.discount_value)

    async def _load_order(self, order_id: UUID, *, lock: Literal["update", "share"]) -> Order:
        query = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
            .with_for_update(of=Order, read=lock == "share")
        )
        order = await self.session.scalar(query)
        if order is None:
            raise ApiError(ErrorCode.ORDER_NOT_FOUND, "Order not found", {"order_id": str(order_id)})
        return order

    @staticmethod
    def _check_owner(order: Order, principal: Principal) -> None:
        if principal.role != UserRole.ADMIN and order.user_id != principal.user_id:
            raise ApiError(
                ErrorCode.ORDER_OWNERSHIP_VIOLATION, "Order belongs to another user", {"order_id": str(order.id)}
            )

    async def _promo_code(self, promo_code_id: UUID | None) -> str | None:
        if promo_code_id is None:
            return None
        return await self.session.scalar(select(PromoCode.code).where(PromoCode.id == promo_code_id))
