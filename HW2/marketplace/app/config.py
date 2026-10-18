from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://marketplace:marketplace@localhost:5432/marketplace"
    db_pool_size: int = Field(10, ge=1)

    jwt_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = "marketplace"
    access_token_ttl_minutes: int = Field(15, ge=15, le=30)
    refresh_token_ttl_days: int = Field(7, ge=7, le=30)

    order_rate_limit_minutes: float = Field(1.0, ge=0)

    admin_email: str | None = None
    admin_password: SecretStr | None = None

    log_level: str = "INFO"
    openapi_path: Path = BASE_DIR / "openapi" / "openapi.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
