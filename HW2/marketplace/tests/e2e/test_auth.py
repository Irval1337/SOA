import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from tests.e2e.conftest import PASSWORD, assert_error

JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me-0123456789abcdef")


def test_register_login_and_tokens(api, db):
    email = f"Mixed.Case-{uuid.uuid4().hex[:8]}@Example.com"
    response = api.post("/auth/register", json={"email": email, "password": PASSWORD, "role": "SELLER"})
    assert response.status_code == 201
    user = response.json()
    assert user["email"] == email.lower()
    assert user["role"] == "SELLER"

    row = db.one("SELECT email, role, password_hash FROM users WHERE id = %s", user["id"])
    assert row["role"] == "SELLER"
    assert row["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in row["password_hash"]

    tokens = api.post("/auth/login", json={"email": email.upper(), "password": PASSWORD}).json()
    assert tokens["token_type"] == "Bearer"
    assert 15 * 60 <= tokens["expires_in"] <= 30 * 60
    assert 7 * 86400 <= tokens["refresh_expires_in"] <= 30 * 86400

    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    assert claims["role"] == "SELLER"
    assert claims["sub"] == user["id"]
    assert claims["type"] == "access"


def test_default_role_is_user(api):
    response = api.post("/auth/register", json={"email": f"u-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD})
    assert response.status_code == 201
    assert response.json()["role"] == "USER"


def test_cannot_register_admin(api):
    response = api.post(
        "/auth/register", json={"email": f"a-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD, "role": "ADMIN"}
    )
    body = assert_error(response, 400, "VALIDATION_ERROR")
    assert body["details"]["violations"][0]["field"] == "role"


def test_role_must_not_be_null(api):
    response = api.post("/auth/register", json={"email": f"n-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD, "role": None})
    body = assert_error(response, 400, "VALIDATION_ERROR")
    assert body["details"]["violations"][0]["field"] == "role"


def test_register_validation(api):
    response = api.post("/auth/register", json={"email": "not-an-email", "password": "short"})
    body = assert_error(response, 400, "VALIDATION_ERROR")
    fields = {v["field"] for v in body["details"]["violations"]}
    assert fields == {"email", "password"}


def test_duplicate_email(api, buyer):
    response = api.post("/auth/register", json={"email": buyer.email.upper(), "password": PASSWORD})
    assert_error(response, 409, "USER_ALREADY_EXISTS")


def test_wrong_password(api, buyer):
    assert_error(api.post("/auth/login", json={"email": buyer.email, "password": "wrong-pass"}), 401, "INVALID_CREDENTIALS")
    assert_error(
        api.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass"}), 401, "INVALID_CREDENTIALS"
    )


def test_refresh_issues_new_access_token(api, buyer):
    response = api.post("/auth/refresh", json={"refresh_token": buyer.refresh_token})
    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed["token_type"] == "Bearer" and refreshed["expires_in"] == 15 * 60
    claims = jwt.decode(refreshed["access_token"], options={"verify_signature": False})
    assert (claims["sub"], claims["role"], claims["type"]) == (buyer.id, "USER", "access")
    assert api.get("/products", refreshed["access_token"]).status_code == 200


def test_refresh_rejects_access_token_garbage_and_expired(api, buyer):
    assert_error(api.post("/auth/refresh", json={"refresh_token": buyer.token}), 401, "REFRESH_TOKEN_INVALID")
    assert_error(api.post("/auth/refresh", json={"refresh_token": "garbage"}), 401, "REFRESH_TOKEN_INVALID")
    issued = datetime.now(UTC) - timedelta(days=8)
    expired = jwt.encode(
        {"iss": "marketplace", "sub": buyer.id, "type": "refresh", "jti": str(uuid.uuid4()),
         "iat": issued, "exp": issued + timedelta(days=7)},
        JWT_SECRET,
        algorithm="HS256",
    )
    assert_error(api.post("/auth/refresh", json={"refresh_token": expired}), 401, "REFRESH_TOKEN_INVALID")


def test_protected_endpoints_require_token(api):
    assert_error(api.get("/products"), 401, "TOKEN_INVALID")
    assert_error(api.get("/products", "not.a.jwt"), 401, "TOKEN_INVALID")
    assert_error(api.get("/products", headers={"Authorization": "Basic dXNlcjpwYXNz"}), 401, "TOKEN_INVALID")


def test_refresh_token_is_not_an_access_token(api, buyer):
    assert_error(api.get("/products", buyer.refresh_token), 401, "TOKEN_INVALID")


def test_forged_signature(api, buyer):
    claims = jwt.decode(buyer.token, options={"verify_signature": False})
    claims["role"] = "ADMIN"
    forged = jwt.encode(claims, "another-secret-another-secret-123456", algorithm="HS256")
    assert_error(api.get("/products", forged), 401, "TOKEN_INVALID")


def test_alg_none_is_rejected(api, buyer):
    claims = jwt.decode(buyer.token, options={"verify_signature": False})
    unsigned = jwt.encode(claims, None, algorithm="none")
    assert_error(api.get("/products", unsigned), 401, "TOKEN_INVALID")


def test_expired_token(api, buyer):
    issued = datetime.now(UTC) - timedelta(hours=1)
    expired = jwt.encode(
        {"iss": "marketplace", "sub": buyer.id, "role": "USER", "type": "access", "jti": str(uuid.uuid4()),
         "iat": issued, "exp": issued + timedelta(minutes=15)},
        JWT_SECRET,
        algorithm="HS256",
    )
    assert_error(api.get("/products", expired), 401, "TOKEN_EXPIRED")


def test_expired_token_tool():
    result = subprocess.run(
        [sys.executable, "-m", "app.tools.expired_token"], capture_output=True, text=True, check=True,
        env={**os.environ, "JWT_SECRET": JWT_SECRET},
    )
    claims = jwt.decode(result.stdout.strip(), JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["exp"] < datetime.now(UTC).timestamp()
