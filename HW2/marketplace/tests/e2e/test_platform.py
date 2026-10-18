import uuid

from tests.e2e.conftest import assert_error


def test_health(api):
    response = api.get("/health")
    assert response.json() == {"status": "ok", "database": "ok"}


def test_every_response_has_request_id(api, buyer):
    ids = set()
    for response in (api.get("/health"), api.get("/products", buyer.token), api.get("/products")):
        uuid.UUID(response.headers["X-Request-Id"])
        ids.add(response.headers["X-Request-Id"])
    assert len(ids) == 3


def test_unknown_route_and_method(api, buyer):
    response = api.get("/no-such-route", validate=False)
    assert_error(response, 404, "NOT_FOUND")
    assert "X-Request-Id" in response.headers
    assert_error(api.request("DELETE", "/orders", buyer.token, validate=False), 405, "METHOD_NOT_ALLOWED")


def test_served_openapi_is_the_contract(api):
    spec = api.get("/openapi.json", validate=False).json()
    assert spec["openapi"] == "3.0.3"
    assert spec["paths"]["/products"]["post"]["x-roles"] == ["SELLER", "ADMIN"]
    assert "ErrorResponse" in spec["components"]["schemas"]
