import asyncio

from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.orm import User
from app.security.tokens import TokenService
from generated.apis import AuthApi
from generated.models import (
    AccessToken,
    ErrorCode,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    TokenType,
    UserResponse,
    UserRole,
)


class AuthService(AuthApi):
    def __init__(self, session: AsyncSession, tokens: TokenService, hasher: PasswordHash) -> None:
        self.session = session
        self.tokens = tokens
        self.hasher = hasher

    async def register(self, *, body: RegisterRequest) -> UserResponse:
        role = UserRole(body.role)
        password_hash = await asyncio.to_thread(self.hasher.hash, body.password)
        user = User(email=body.email.lower(), password_hash=password_hash, role=role)
        try:
            async with self.session.begin():
                self.session.add(user)
        except IntegrityError:
            raise ApiError(ErrorCode.USER_ALREADY_EXISTS, "User with this email already exists") from None
        return UserResponse.model_validate(user, from_attributes=True)

    async def login(self, *, body: LoginRequest) -> TokenPair:
        async with self.session.begin():
            user = await self.session.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
        if user is None or not await asyncio.to_thread(self.hasher.verify, body.password, user.password_hash):
            raise ApiError(ErrorCode.INVALID_CREDENTIALS, "Invalid email or password")
        access = self.tokens.issue_access(user.id, user.role)
        refresh = self.tokens.issue_refresh(user.id)
        return TokenPair(
            access_token=access.token,
            refresh_token=refresh.token,
            token_type=TokenType.Bearer,
            expires_in=access.ttl_seconds,
            refresh_expires_in=refresh.ttl_seconds,
        )

    async def refresh_tokens(self, *, body: RefreshRequest) -> AccessToken:
        user_id = self.tokens.decode_refresh(body.refresh_token)
        async with self.session.begin():
            user = await self.session.get(User, user_id)
        if user is None:
            raise ApiError(ErrorCode.REFRESH_TOKEN_INVALID, "Refresh token is invalid or expired")
        access = self.tokens.issue_access(user.id, user.role)
        return AccessToken(access_token=access.token, token_type=TokenType.Bearer, expires_in=access.ttl_seconds)
