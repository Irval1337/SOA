from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, FetchedValue, ForeignKey, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from generated.models import DiscountType, OrderStatus, ProductStatus, UserRole


class OperationType(StrEnum):
    CREATE_ORDER = "CREATE_ORDER"
    UPDATE_ORDER = "UPDATE_ORDER"


def pg_enum(enum_cls: type[PyEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, create_type=False, values_callable=lambda e: [m.value for m in e])


Money = Numeric(12, 2, asdecimal=True)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), server_onupdate=FetchedValue())


class User(Timestamps, Base):
    __tablename__ = "users"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"))


class Product(Timestamps, Base):
    __tablename__ = "products"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(4000))
    price: Mapped[Decimal] = mapped_column(Money)
    stock: Mapped[int]
    category: Mapped[str] = mapped_column(String(100))
    status: Mapped[ProductStatus] = mapped_column(pg_enum(ProductStatus, "product_status"))
    seller_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class PromoCode(Base):
    __tablename__ = "promo_codes"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(20))
    discount_type: Mapped[DiscountType] = mapped_column(pg_enum(DiscountType, "discount_type"))
    discount_value: Mapped[Decimal] = mapped_column(Money)
    min_order_amount: Mapped[Decimal] = mapped_column(Money)
    max_uses: Mapped[int]
    current_uses: Mapped[int] = mapped_column(default=0)
    valid_from: Mapped[datetime]
    valid_until: Mapped[datetime]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Order(Timestamps, Base):
    __tablename__ = "orders"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[OrderStatus] = mapped_column(pg_enum(OrderStatus, "order_status"))
    promo_code_id: Mapped[UUID | None] = mapped_column(ForeignKey("promo_codes.id"))
    total_amount: Mapped[Decimal] = mapped_column(Money)
    discount_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))

    items: Mapped[list["OrderItem"]] = relationship(
        cascade="all, delete-orphan", lazy="raise", order_by="OrderItem.product_id"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    price_at_order: Mapped[Decimal] = mapped_column(Money)


class UserOperation(Base):
    __tablename__ = "user_operations"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    operation_type: Mapped[OperationType] = mapped_column(pg_enum(OperationType, "operation_type"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
