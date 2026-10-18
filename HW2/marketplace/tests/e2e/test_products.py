import time
import uuid

import pytest

from tests.e2e.conftest import assert_error, money


def test_crud_and_soft_delete(api, db, seller, buyer):
    created = api.post(
        "/products",
        seller.token,
        json={"name": "Lamp", "description": None, "price": 1999.99, "stock": 3, "category": "home", "status": "ACTIVE"},
    )
    assert created.status_code == 201
    product = created.json()
    assert product["seller_id"] == seller.id
    assert product["description"] is None
    assert money(product["price"]) == money("1999.99")
    assert product["created_at"] == product["updated_at"]

    assert api.get(f"/products/{product['id']}", buyer.token).json() == product

    time.sleep(0.01)
    updated = api.put(
        f"/products/{product['id']}",
        seller.token,
        json={"name": "Lamp 2", "description": "LED", "price": 2500, "stock": 5, "category": "home", "status": "INACTIVE"},
    ).json()
    assert updated["name"] == "Lamp 2" and updated["status"] == "INACTIVE" and updated["stock"] == 5
    assert updated["created_at"] == product["created_at"]
    assert updated["updated_at"] > product["updated_at"]

    deleted = api.delete(f"/products/{product['id']}", seller.token)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "ARCHIVED"

    row = db.one("SELECT status, name FROM products WHERE id = %s", product["id"])
    assert row == {"status": "ARCHIVED", "name": "Lamp 2"}
    assert api.get(f"/products/{product['id']}", buyer.token).json()["status"] == "ARCHIVED"
    assert api.delete(f"/products/{product['id']}", seller.token).json()["status"] == "ARCHIVED"


def test_pagination_and_filters(api, seller, buyer, make_product):
    category = f"pg-{uuid.uuid4().hex[:8]}"
    ids = [make_product(seller, category=category, name=f"p{i}")["id"] for i in range(5)]
    make_product(seller, category=category, status="INACTIVE")

    first = api.get("/products", buyer.token, params={"category": category, "status": "ACTIVE", "page": 0, "size": 2})
    page = first.json()
    assert page["totalElements"] == 5 and page["totalPages"] == 3
    assert page["page"] == 0 and page["size"] == 2
    assert [p["id"] for p in page["items"]] == ids[::-1][:2]

    last = api.get("/products", buyer.token, params={"category": category, "status": "ACTIVE", "page": 2, "size": 2})
    assert [p["id"] for p in last.json()["items"]] == [ids[0]]

    beyond = api.get("/products", buyer.token, params={"category": category, "page": 50}).json()
    assert beyond["items"] == [] and beyond["totalElements"] == 6

    inactive = api.get("/products", buyer.token, params={"category": category, "status": "INACTIVE"}).json()
    assert inactive["totalElements"] == 1

    default = api.get("/products", buyer.token, params={"category": category}).json()
    assert default["size"] == 20 and default["page"] == 0


@pytest.mark.parametrize(
    "params, field",
    [
        ({"page": -1}, "page"),
        ({"size": 0}, "size"),
        ({"size": 101}, "size"),
        ({"size": "abc"}, "size"),
        ({"status": "DELETED"}, "status"),
        ({"category": ""}, "category"),
        ({"category": "a\x00b"}, "category"),
        ({"page": 2**31}, "page"),
    ],
)
def test_list_validation(api, buyer, params, field):
    violation = assert_error(api.get("/products", buyer.token, params=params), 400, "VALIDATION_ERROR")["details"]["violations"][0]
    assert (violation["field"], violation["location"]) == (field, "query")


VALID = {"name": "Item", "price": 10, "stock": 1, "category": "c", "status": "ACTIVE"}


@pytest.mark.parametrize(
    "patch, field, code",
    [
        ({"name": ""}, "name", "string_too_short"),
        ({"name": "x" * 256}, "name", "string_too_long"),
        ({"name": "   "}, "name", "string_pattern_mismatch"),
        ({"name": "a\x00b"}, "name", "string_pattern_mismatch"),
        ({"description": "text\x00"}, "description", "string_pattern_mismatch"),
        ({"description": "x" * 4001}, "description", "string_too_long"),
        ({"price": 0}, "price", "greater_than_equal"),
        ({"price": -5}, "price", "greater_than_equal"),
        ({"price": 10.001}, "price", "multiple_of"),
        ({"price": "abc"}, "price", "decimal_parsing"),
        ({"stock": -1}, "stock", "greater_than_equal"),
        ({"stock": 1.5}, "stock", "int_from_float"),
        ({"stock": 1_000_001}, "stock", "less_than_equal"),
        ({"category": ""}, "category", "string_too_short"),
        ({"category": "c" * 101}, "category", "string_too_long"),
        ({"status": "SOLD"}, "status", "enum"),
        ({"seller_id": str(uuid.uuid4())}, "seller_id", "extra_forbidden"),
    ],
)
def test_create_validation(api, seller, patch, field, code):
    body = assert_error(api.post("/products", seller.token, json={**VALID, **patch}), 400, "VALIDATION_ERROR")
    assert {"field": field, "location": "body", "code": code}.items() <= body["details"]["violations"][0].items()


def test_lone_surrogate_is_rejected(api, seller):
    body = b'{"name": "\\ud800", "price": 1, "stock": 1, "category": "c", "status": "ACTIVE"}'
    response = api.post("/products", seller.token, content=body, headers={"Content-Type": "application/json"})
    violation = assert_error(response, 400, "VALIDATION_ERROR")["details"]["violations"][0]
    assert (violation["field"], violation["code"]) == ("name", "string_unicode")


def test_missing_required_fields(api, seller):
    body = assert_error(api.post("/products", seller.token, json={}), 400, "VALIDATION_ERROR")
    assert {v["field"] for v in body["details"]["violations"]} == {"name", "price", "stock", "category", "status"}
    assert {v["code"] for v in body["details"]["violations"]} == {"missing"}


def test_boundaries_are_accepted(api, seller):
    response = api.post(
        "/products",
        seller.token,
        json={"name": "x" * 255, "description": "d" * 4000, "price": 0.01, "stock": 0, "category": "c" * 100,
              "status": "INACTIVE"},
    )
    assert response.status_code == 201
    assert money(response.json()["price"]) == money("0.01")


def test_malformed_json(api, seller):
    response = api.post(
        "/products", seller.token, content=b'{"name": "x",', headers={"Content-Type": "application/json"}
    )
    body = assert_error(response, 400, "VALIDATION_ERROR")
    assert body["details"]["violations"][0]["code"] == "json_invalid"


def test_not_found_and_bad_id(api, buyer):
    assert_error(api.get(f"/products/{uuid.uuid4()}", buyer.token), 404, "PRODUCT_NOT_FOUND")
    body = assert_error(api.get("/products/not-a-uuid", buyer.token), 400, "VALIDATION_ERROR")
    assert body["details"]["violations"][0]["location"] == "path"


def test_role_matrix(api, seller, buyer, admin, register, make_product):
    product = make_product(seller)
    other_seller = register("SELLER")
    update = {**VALID, "name": "changed"}

    assert_error(api.post("/products", buyer.token, json=VALID), 403, "ACCESS_DENIED")
    assert_error(api.put(f"/products/{product['id']}", buyer.token, json=update), 403, "ACCESS_DENIED")
    assert_error(api.delete(f"/products/{product['id']}", buyer.token), 403, "ACCESS_DENIED")

    assert_error(api.put(f"/products/{product['id']}", other_seller.token, json=update), 403, "ACCESS_DENIED")
    assert_error(api.delete(f"/products/{product['id']}", other_seller.token), 403, "ACCESS_DENIED")

    assert api.put(f"/products/{product['id']}", admin.token, json=update).json()["name"] == "changed"
    assert api.delete(f"/products/{product['id']}", admin.token).json()["status"] == "ARCHIVED"
    assert api.post("/products", admin.token, json=VALID).status_code == 201


def test_update_unknown_product(api, seller):
    assert_error(api.put(f"/products/{uuid.uuid4()}", seller.token, json=VALID), 404, "PRODUCT_NOT_FOUND")
    assert_error(api.delete(f"/products/{uuid.uuid4()}", seller.token), 404, "PRODUCT_NOT_FOUND")
