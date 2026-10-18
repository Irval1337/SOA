import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from generated.apis import SystemApi
from generated.models import ErrorCode, HealthResponse

logger = logging.getLogger(__name__)


class SystemService(SystemApi):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def health_check(self) -> HealthResponse:
        try:
            await self.session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            logger.warning("Health check: database is unavailable", exc_info=True)
            raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, "Database is unavailable") from None
        return HealthResponse(status="ok", database="ok")
