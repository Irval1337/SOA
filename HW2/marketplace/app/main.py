import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial
from typing import Any

import yaml
from fastapi import Depends, FastAPI, Request
from pwdlib import PasswordHash
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory, get_session
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.orm import User
from app.security.dependencies import authorize
from app.security.tokens import TokenService
from app.services.auth import AuthService
from app.services.orders import OrderService
from app.services.products import ProductService
from app.services.promo_codes import PromoCodeService
from app.services.system import SystemService
from generated.apis import (
    AuthApi,
    OrdersApi,
    ProductsApi,
    PromoCodesApi,
    SystemApi,
    create_auth_router,
    create_orders_router,
    create_products_router,
    create_promo_codes_router,
    create_system_router,
)
from generated.models import UserRole

logger = logging.getLogger("marketplace")


def auth_service(request: Request, session: AsyncSession = Depends(get_session)) -> AuthApi:
    return AuthService(session, request.app.state.tokens, request.app.state.hasher)


def product_service(session: AsyncSession = Depends(get_session)) -> ProductsApi:
    return ProductService(session)


def order_service(request: Request, session: AsyncSession = Depends(get_session)) -> OrdersApi:
    settings: Settings = request.app.state.settings
    return OrderService(session, timedelta(minutes=settings.order_rate_limit_minutes))


def promo_code_service(session: AsyncSession = Depends(get_session)) -> PromoCodesApi:
    return PromoCodeService(session)


def system_service(session: AsyncSession = Depends(get_session)) -> SystemApi:
    return SystemService(session)


async def ensure_admin(session_factory: async_sessionmaker[AsyncSession], settings: Settings, hasher: PasswordHash) -> None:
    if not settings.admin_email or not settings.admin_password:
        return
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(User)
            .values(
                email=settings.admin_email.lower(),
                password_hash=hasher.hash(settings.admin_password.get_secret_value()),
                role=UserRole.ADMIN,
            )
            .on_conflict_do_nothing()
        )
    logger.info("Admin account is ready", extra={"admin_email": settings.admin_email.lower()})


def contract_openapi(app: FastAPI, settings: Settings) -> dict[str, Any]:
    if app.openapi_schema is None:
        app.openapi_schema = yaml.safe_load(settings.openapi_path.read_text(encoding="utf-8"))
    return app.openapi_schema


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.session_factory = create_session_factory(engine)
        await ensure_admin(app.state.session_factory, settings, app.state.hasher)
        logger.info(
            "Marketplace API started",
            extra={"order_rate_limit_minutes": settings.order_rate_limit_minutes},
        )
        yield
        await engine.dispose()

    app = FastAPI(title="Marketplace API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.tokens = TokenService(settings)
    app.state.hasher = PasswordHash.recommended()
    app.openapi = partial(contract_openapi, app, settings)

    app.include_router(create_system_router(system_service))
    app.include_router(create_auth_router(auth_service))
    app.include_router(create_products_router(product_service, authorize))
    app.include_router(create_orders_router(order_service, authorize))
    app.include_router(create_promo_codes_router(promo_code_service, authorize))

    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    return app
