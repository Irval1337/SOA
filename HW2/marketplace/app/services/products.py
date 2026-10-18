import math
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.orm import Product
from generated.apis import ProductsApi
from generated.models import (
    ErrorCode,
    ProductCreate,
    ProductPage,
    ProductResponse,
    ProductStatus,
    ProductUpdate,
    UserRole,
)
from generated.security import Principal


class ProductService(ProductsApi):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_products(
        self,
        *,
        principal: Principal,
        page: int | None,
        size: int | None,
        status: ProductStatus | None,
        category: str | None,
    ) -> ProductPage:
        page = page or 0
        size = size or 20
        filters = []
        if status is not None:
            filters.append(Product.status == status)
        if category is not None:
            filters.append(Product.category == category)
        async with self.session.begin():
            total = await self.session.scalar(select(func.count()).select_from(Product).where(*filters))
            rows = await self.session.scalars(
                select(Product)
                .where(*filters)
                .order_by(Product.created_at.desc(), Product.id.desc())
                .offset(page * size)
                .limit(size)
            )
            items = [ProductResponse.model_validate(p, from_attributes=True) for p in rows]
        return ProductPage(
            items=items, totalElements=total, totalPages=math.ceil(total / size), page=page, size=size
        )

    async def create_product(self, *, principal: Principal, body: ProductCreate) -> ProductResponse:
        product = Product(**body.model_dump(), seller_id=principal.user_id)
        try:
            async with self.session.begin():
                self.session.add(product)
        except IntegrityError:
            raise ApiError(ErrorCode.TOKEN_INVALID, "User of the access token does not exist") from None
        return ProductResponse.model_validate(product, from_attributes=True)

    async def get_product(self, *, principal: Principal, id: UUID) -> ProductResponse:
        async with self.session.begin():
            product = await self._get(id)
            return ProductResponse.model_validate(product, from_attributes=True)

    async def update_product(self, *, principal: Principal, id: UUID, body: ProductUpdate) -> ProductResponse:
        async with self.session.begin():
            product = await self._get_for_change(id, principal)
            for field, value in body.model_dump().items():
                setattr(product, field, value)
        return ProductResponse.model_validate(product, from_attributes=True)

    async def delete_product(self, *, principal: Principal, id: UUID) -> ProductResponse:
        async with self.session.begin():
            product = await self._get_for_change(id, principal)
            product.status = ProductStatus.ARCHIVED
        return ProductResponse.model_validate(product, from_attributes=True)

    async def _get(self, product_id: UUID, *, for_update: bool = False) -> Product:
        product = await self.session.get(Product, product_id, with_for_update=for_update)
        if product is None:
            raise ApiError(ErrorCode.PRODUCT_NOT_FOUND, "Product not found", {"product_ids": [str(product_id)]})
        return product

    async def _get_for_change(self, product_id: UUID, principal: Principal) -> Product:
        product = await self._get(product_id, for_update=True)
        if principal.role == UserRole.SELLER and product.seller_id != principal.user_id:
            raise ApiError(
                ErrorCode.ACCESS_DENIED,
                "Sellers can modify only their own products",
                {"product_id": str(product_id)},
            )
        return product
