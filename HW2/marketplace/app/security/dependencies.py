from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, SecurityScopes

from app.context import bind_user
from app.errors import ApiError
from app.security.tokens import TokenService
from generated.models import ErrorCode
from generated.security import Principal, bearer_auth


async def authorize(
    request: Request,
    security_scopes: SecurityScopes,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_auth),
) -> Principal:
    if credentials is None:
        raise ApiError(ErrorCode.TOKEN_INVALID, "Missing bearer token")
    tokens: TokenService = request.app.state.tokens
    claims = tokens.decode_access(credentials.credentials)
    principal = Principal(user_id=claims.user_id, role=claims.role)
    bind_user(principal.user_id)
    if security_scopes.scopes and principal.role.value not in security_scopes.scopes:
        raise ApiError(
            ErrorCode.ACCESS_DENIED,
            f"Role {principal.role.value} is not allowed to perform this operation",
            {"role": principal.role.value, "allowed_roles": security_scopes.scopes},
        )
    return principal
