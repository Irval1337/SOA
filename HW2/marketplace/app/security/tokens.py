from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

import jwt

from app.config import Settings
from app.errors import ApiError
from generated.models import ErrorCode, UserRole

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "type", "jti", "iat", "exp", "iss"]


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True)
class IssuedToken:
    token: str
    ttl_seconds: int


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    role: UserRole


class TokenService:
    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret.get_secret_value()
        self._issuer = settings.jwt_issuer
        self.access_ttl = timedelta(minutes=settings.access_token_ttl_minutes)
        self.refresh_ttl = timedelta(days=settings.refresh_token_ttl_days)

    def _issue(self, user_id: UUID, token_type: TokenType, ttl: timedelta, extra: dict | None = None) -> IssuedToken:
        now = datetime.now(UTC)
        payload = {
            "iss": self._issuer,
            "sub": str(user_id),
            "type": token_type.value,
            "jti": str(uuid4()),
            "iat": now,
            "exp": now + ttl,
            **(extra or {}),
        }
        token = jwt.encode(payload, self._secret, algorithm=ALGORITHM)
        return IssuedToken(token=token, ttl_seconds=int(ttl.total_seconds()))

    def issue_access(self, user_id: UUID, role: UserRole) -> IssuedToken:
        return self._issue(user_id, TokenType.ACCESS, self.access_ttl, {"role": role.value})

    def issue_refresh(self, user_id: UUID) -> IssuedToken:
        return self._issue(user_id, TokenType.REFRESH, self.refresh_ttl)

    def _decode(self, token: str) -> dict:
        return jwt.decode(
            token,
            self._secret,
            algorithms=[ALGORITHM],
            issuer=self._issuer,
            options={"require": REQUIRED_CLAIMS},
        )

    def decode_access(self, token: str) -> AccessClaims:
        try:
            payload = self._decode(token)
        except jwt.ExpiredSignatureError:
            raise ApiError(ErrorCode.TOKEN_EXPIRED, "Access token has expired") from None
        except jwt.InvalidTokenError:
            raise ApiError(ErrorCode.TOKEN_INVALID, "Access token is invalid") from None
        try:
            if payload["type"] != TokenType.ACCESS:
                raise ValueError("not an access token")
            return AccessClaims(user_id=UUID(payload["sub"]), role=UserRole(payload["role"]))
        except (KeyError, ValueError):
            raise ApiError(ErrorCode.TOKEN_INVALID, "Access token is invalid") from None

    def decode_refresh(self, token: str) -> UUID:
        try:
            payload = self._decode(token)
            if payload["type"] != TokenType.REFRESH:
                raise ValueError("not a refresh token")
            return UUID(payload["sub"])
        except (jwt.InvalidTokenError, KeyError, ValueError):
            raise ApiError(ErrorCode.REFRESH_TOKEN_INVALID, "Refresh token is invalid or expired") from None
