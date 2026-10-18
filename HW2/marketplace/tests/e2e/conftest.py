import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from tests.e2e.contract import ContractValidator

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://marketplace:marketplace@localhost:5433/marketplace")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminPassw0rd")
PASSWORD = "Str0ngPassw0rd"


class Api:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=30)
        self.contract = ContractValidator(base_url)

    def request(
        self, method: str, path: str, token: str | None = None, *, validate: bool = True, **kwargs: Any
    ) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        response = self.client.request(method, path, headers=headers, **kwargs)
        if validate:
            self.contract.validate(response)
        return response

    def get(self, path: str, token: str | None = None, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, token, **kwargs)

    def post(self, path: str, token: str | None = None, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, token, **kwargs)

    def put(self, path: str, token: str | None = None, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, token, **kwargs)

    def patch(self, path: str, token: str | None = None, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, token, **kwargs)

    def delete(self, path: str, token: str | None = None, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, token, **kwargs)


@dataclass
class Account:
    id: str
    email: str
    role: str
    token: str
    refresh_token: str


class Db:
    def __init__(self, url: str) -> None:
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        return self.conn.execute(sql, params).fetchall()

    def execute(self, sql: str, *params: Any) -> None:
        self.conn.execute(sql, params)

    def forget_rate_limit(self, user_id: str) -> None:
        self.execute(
            "UPDATE user_operations SET created_at = created_at - interval '1 hour' WHERE user_id = %s", user_id
        )


def assert_error(response: httpx.Response, status: int, error_code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error_code"] == error_code, body
    assert isinstance(body["message"], str) and body["message"]
    return body


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


@pytest.fixture(scope="session")
def api() -> Api:
    return Api(BASE_URL)


@pytest.fixture(scope="session")
def db() -> Db:
    return Db(DATABASE_URL)


def _login(api: Api, email: str, password: str) -> dict[str, Any]:
    response = api.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="session")
def register(api: Api):
    def _register(role: str = "USER") -> Account:
        email = f"{role.lower()}-{uuid.uuid4().hex[:12]}@example.com"
        response = api.post("/auth/register", json={"email": email, "password": PASSWORD, "role": role})
        assert response.status_code == 201, response.text
        tokens = _login(api, email, PASSWORD)
        return Account(response.json()["id"], email, role, tokens["access_token"], tokens["refresh_token"])

    return _register


@pytest.fixture(scope="session")
def admin(api: Api) -> Account:
    tokens = _login(api, ADMIN_EMAIL, ADMIN_PASSWORD)
    return Account("", ADMIN_EMAIL, "ADMIN", tokens["access_token"], tokens["refresh_token"])


@pytest.fixture
def seller(register) -> Account:
    return register("SELLER")


@pytest.fixture
def buyer(register) -> Account:
    return register("USER")


@pytest.fixture
def make_product(api: Api):
    def _make(owner: Account, **overrides: Any) -> dict[str, Any]:
        body = {
            "name": "Mechanical keyboard",
            "description": "87 keys",
            "price": 100.00,
            "stock": 10,
            "category": f"cat-{uuid.uuid4().hex[:8]}",
            "status": "ACTIVE",
            **overrides,
        }
        response = api.post("/products", owner.token, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    return _make


@pytest.fixture
def make_promo(api: Api, admin: Account):
    def _make(**overrides: Any) -> dict[str, Any]:
        body = {
            "code": f"P{uuid.uuid4().hex[:10].upper()}",
            "discount_type": "PERCENTAGE",
            "discount_value": 10,
            "min_order_amount": 0,
            "max_uses": 100,
            "valid_from": "2020-01-01T00:00:00Z",
            "valid_until": "2099-01-01T00:00:00Z",
            "active": True,
            **overrides,
        }
        response = api.post("/promo-codes", admin.token, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    return _make
