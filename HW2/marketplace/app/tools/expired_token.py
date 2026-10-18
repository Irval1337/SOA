import sys
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings
from app.security.tokens import ALGORITHM, TokenType


def main() -> None:
    settings = get_settings()
    user_id = sys.argv[1] if len(sys.argv) > 1 else str(uuid.uuid4())
    role = sys.argv[2] if len(sys.argv) > 2 else "USER"
    issued_at = datetime.now(UTC) - timedelta(minutes=settings.access_token_ttl_minutes + 1)
    payload = {
        "iss": settings.jwt_issuer,
        "sub": user_id,
        "type": TokenType.ACCESS.value,
        "role": role,
        "jti": str(uuid.uuid4()),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    print(jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM))


if __name__ == "__main__":
    main()
