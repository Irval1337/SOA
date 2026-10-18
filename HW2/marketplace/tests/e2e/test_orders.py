import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.e2e.conftest import assert_error, money


def order_body(*items, promo_code=None):
    body = {"items": [{"product_id": product["id"], "quantity": quantity} for product, quantity in items]}
    if promo_code is not None:
        body["promo_code"] = promo_code
    return body


def stock(db, product):
    return db.one("SELECT stock FROM products WHERE id = %s", product["id"])["stock"]


def promo_uses(db, promo):
    return db.one("SELECT current_uses FROM promo_codes WHERE id = %s", promo["id"])["current_uses"]


def test_create_order_reserves_stock_and_snapshots_price(api, db, seller, buyer, make_product):
    keyboard = make_product(seller, price=100.50, stock=10)
    mouse = make_product(seller, price=25, stock=5)

    response = api.post("/orders", buyer.token, json=order_body((keyboard, 2), (mouse, 1)))
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "CREATED"
    assert order["user_id"] == buyer.id
    assert money(order["subtotal_amount"]) == money("226.00")
    assert money(order["total_amount"]) == money("226.00")
    assert money(order["discount_amount"]) == 0
    assert order["promo_code"] is None

    assert stock(db, keyboard) == 8 and stock(db, mouse) == 4
    items = db.all("SELECT product_id::text, quantity, price_at_order FROM order_items WHERE order_id = %s", order["id"])
    assert {(i["product_id"], i["quantity"], money(i["price_at_order"])) for i in items} == {
        (keyboard["id"], 2, money("100.50")),
        (mouse["id"], 1, money("25.00")),
    }
    ops = db.all("SELECT operation_type FROM user_operations WHERE user_id = %s", buyer.id)
    assert [op["operation_type"] for op in ops] == ["CREATE_ORDER"]

    api.put(
        f"/products/{keyboard['id']}",
        seller.token,
        json={"name": "k", "price": 999, "stock": 8, "category": "c", "status": "ACTIVE"},
    )
    again = api.get(f"/orders/{order['id']}", buyer.token).json()
    assert money(again["total_amount"]) == money("226.00")
    assert {money(i["price_at_order"]) for i in again["items"]} == {money("100.50"), money("25.00")}


def test_rate_limit_is_checked_first(api, db, seller, buyer, make_product):
    product = make_product(seller)
    assert api.post("/orders", buyer.token, json=order_body((product, 1))).status_code == 201
    response = api.post("/orders", buyer.token, json=order_body((product, 1)))
    body = assert_error(response, 429, "ORDER_LIMIT_EXCEEDED")
    assert int(response.headers["Retry-After"]) >= 1
    assert body["details"]["operation_type"] == "CREATE_ORDER"
    assert stock(db, product) == 9
    assert db.one("SELECT count(*) AS n FROM orders WHERE user_id = %s", buyer.id)["n"] == 1


def test_active_order_blocks_new_one(api, db, seller, buyer, admin, make_product):
    product = make_product(seller)
    first = api.post("/orders", buyer.token, json=order_body((product, 1))).json()
    db.forget_rate_limit(buyer.id)
    body = assert_error(api.post("/orders", buyer.token, json=order_body((product, 1))), 409, "ORDER_HAS_ACTIVE")
    assert body["details"]["order_id"] == first["id"]

    api.patch(f"/orders/{first['id']}/status", admin.token, json={"status": "PAYMENT_PENDING"})
    assert_error(api.post("/orders", buyer.token, json=order_body((product, 1))), 409, "ORDER_HAS_ACTIVE")

    api.patch(f"/orders/{first['id']}/status", admin.token, json={"status": "PAID"})
    assert api.post("/orders", buyer.token, json=order_body((product, 1))).status_code == 201


def test_unknown_and_inactive_products(api, db, seller, buyer, make_product):
    active = make_product(seller)
    inactive = make_product(seller, status="INACTIVE")
    archived = make_product(seller)
    api.delete(f"/products/{archived['id']}", seller.token)
    missing = {"id": str(uuid.uuid4())}

    body = assert_error(api.post("/orders", buyer.token, json=order_body((active, 1), (missing, 1))), 404, "PRODUCT_NOT_FOUND")
    assert body["details"]["product_ids"] == [missing["id"]]

    body = assert_error(api.post("/orders", buyer.token, json=order_body((active, 1), (inactive, 1))), 409, "PRODUCT_INACTIVE")
    assert body["details"]["product_ids"] == [inactive["id"]]
    assert_error(api.post("/orders", buyer.token, json=order_body((archived, 1))), 409, "PRODUCT_INACTIVE")
    assert stock(db, active) == 10


def test_insufficient_stock_lists_every_product(api, db, seller, buyer, make_product):
    a = make_product(seller, stock=3)
    b = make_product(seller, stock=1)
    c = make_product(seller, stock=100)
    response = api.post("/orders", buyer.token, json=order_body((a, 5), (b, 2), (c, 1)))
    body = assert_error(response, 409, "INSUFFICIENT_STOCK")
    shortages = {i["product_id"]: (i["requested"], i["available"]) for i in body["details"]["items"]}
    assert shortages == {a["id"]: (5, 3), b["id"]: (2, 1)}
    assert (stock(db, a), stock(db, b), stock(db, c)) == (3, 1, 100)


def test_order_exactly_the_whole_stock(api, db, seller, buyer, make_product):
    product = make_product(seller, stock=4)
    assert api.post("/orders", buyer.token, json=order_body((product, 4))).status_code == 201
    assert stock(db, product) == 0


@pytest.mark.parametrize(
    "body, field, code",
    [
        ({"items": []}, "items", "too_short"),
        ({"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}] * 51}, "items", "too_long"),
        ({"items": [{"product_id": str(uuid.uuid4()), "quantity": 0}]}, "items[0].quantity", "greater_than_equal"),
        ({"items": [{"product_id": str(uuid.uuid4()), "quantity": 1000}]}, "items[0].quantity", "less_than_equal"),
        ({"items": [{"product_id": "nope", "quantity": 1}]}, "items[0].product_id", "uuid_parsing"),
        ({"items": [{"product_id": str(uuid.uuid4())}]}, "items[0].quantity", "missing"),
        ({"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}], "promo_code": "bad"}, "promo_code",
         "string_pattern_mismatch"),
        ({"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}], "promo_code": "lower_case"}, "promo_code",
         "string_pattern_mismatch"),
        ({"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}], "promo_code": "A" * 21}, "promo_code",
         "string_pattern_mismatch"),
        ({}, "items", "missing"),
    ],
)
def test_create_validation(api, db, buyer, body, field, code):
    response = api.post("/orders", buyer.token, json=body)
    details = assert_error(response, 400, "VALIDATION_ERROR")["details"]
    assert {"field": field, "location": "body", "code": code}.items() <= details["violations"][0].items()
    assert db.one("SELECT count(*) AS n FROM user_operations WHERE user_id = %s", buyer.id)["n"] == 0


def test_duplicate_products_are_rejected(api, seller, buyer, make_product):
    product = make_product(seller)
    body = assert_error(api.post("/orders", buyer.token, json=order_body((product, 1), (product, 2))), 400, "VALIDATION_ERROR")
    assert body["details"]["violations"][0]["field"] == "items[1].product_id"


def test_concurrent_orders_never_oversell(api, db, seller, register, make_product):
    product = make_product(seller, stock=5)
    buyers = [register("USER") for _ in range(10)]

    def place(account):
        return api.post("/orders", account.token, json=order_body((product, 1))).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(place, buyers))
    assert sorted(statuses) == [201] * 5 + [409] * 5
    assert stock(db, product) == 0


def test_concurrent_requests_of_one_user_create_one_order(api, db, seller, buyer, make_product):
    product = make_product(seller, stock=100)
    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(lambda _: api.post("/orders", buyer.token, json=order_body((product, 1))).status_code, range(5)))
    assert sorted(statuses) == [201] + [429] * 4
    assert stock(db, product) == 99


def test_percentage_promo(api, db, seller, buyer, make_product, make_promo):
    product = make_product(seller, price=200)
    promo = make_promo(discount_type="PERCENTAGE", discount_value=15, min_order_amount=300)
    order = api.post("/orders", buyer.token, json=order_body((product, 2), promo_code=promo["code"])).json()
    assert money(order["subtotal_amount"]) == money(400)
    assert money(order["discount_amount"]) == money(60)
    assert money(order["total_amount"]) == money(340)
    assert order["promo_code"] == promo["code"]
    assert promo_uses(db, promo) == 1
    row = db.one("SELECT promo_code_id::text, total_amount, discount_amount FROM orders WHERE id = %s", order["id"])
    assert row["promo_code_id"] == promo["id"] and money(row["discount_amount"]) == money(60)


def test_percentage_promo_rounding(api, seller, buyer, make_product, make_promo):
    product = make_product(seller, price=33.33)
    promo = make_promo(discount_type="PERCENTAGE", discount_value=12.5)
    order = api.post("/orders", buyer.token, json=order_body((product, 1), promo_code=promo["code"])).json()
    assert money(order["discount_amount"]) == money("4.17")
    assert money(order["total_amount"]) == money("29.16")


def test_percentage_promo_above_70_percent_is_not_applied(api, db, seller, register, make_product, make_promo):
    product = make_product(seller, price=100)
    seventy = make_promo(discount_type="PERCENTAGE", discount_value=70)
    order = api.post("/orders", register("USER").token, json=order_body((product, 1), promo_code=seventy["code"])).json()
    assert money(order["total_amount"]) == money(30)

    greedy = make_promo(discount_type="PERCENTAGE", discount_value=80)
    order = api.post("/orders", register("USER").token, json=order_body((product, 1), promo_code=greedy["code"])).json()
    assert money(order["total_amount"]) == money(100)
    assert money(order["discount_amount"]) == 0
    assert order["promo_code"] is None
    assert promo_uses(db, greedy) == 0


def test_fixed_amount_promo(api, seller, register, make_product, make_promo):
    product = make_product(seller, price=50)
    promo = make_promo(discount_type="FIXED_AMOUNT", discount_value=30)
    order = api.post("/orders", register("USER").token, json=order_body((product, 2), promo_code=promo["code"])).json()
    assert money(order["total_amount"]) == money(70)

    huge = make_promo(discount_type="FIXED_AMOUNT", discount_value=500)
    order = api.post("/orders", register("USER").token, json=order_body((product, 1), promo_code=huge["code"])).json()
    assert money(order["discount_amount"]) == money(50)
    assert money(order["total_amount"]) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"valid_from": "2099-01-01T00:00:00Z", "valid_until": "2099-02-01T00:00:00Z"},
        {"valid_from": "2020-01-01T00:00:00Z", "valid_until": "2020-02-01T00:00:00Z"},
    ],
    ids=["inactive", "not-started", "expired"],
)
def test_invalid_promo(api, db, seller, buyer, make_product, make_promo, overrides):
    product = make_product(seller)
    promo = make_promo(**overrides)
    response = api.post("/orders", buyer.token, json=order_body((product, 1), promo_code=promo["code"]))
    assert_error(response, 422, "PROMO_CODE_INVALID")
    assert stock(db, product) == 10


def test_unknown_and_exhausted_promo(api, db, seller, register, make_product, make_promo):
    product = make_product(seller)
    assert_error(
        api.post("/orders", register("USER").token, json=order_body((product, 1), promo_code="NO_SUCH_CODE")),
        422,
        "PROMO_CODE_INVALID",
    )
    promo = make_promo(max_uses=1)
    assert api.post("/orders", register("USER").token, json=order_body((product, 1), promo_code=promo["code"])).status_code == 201
    assert promo_uses(db, promo) == 1
    assert_error(
        api.post("/orders", register("USER").token, json=order_body((product, 1), promo_code=promo["code"])),
        422,
        "PROMO_CODE_INVALID",
    )


def test_promo_min_amount(api, seller, buyer, make_product, make_promo):
    product = make_product(seller, price=100)
    promo = make_promo(min_order_amount=250)
    body = assert_error(
        api.post("/orders", buyer.token, json=order_body((product, 2), promo_code=promo["code"])), 422, "PROMO_CODE_MIN_AMOUNT"
    )
    assert money(body["details"]["min_order_amount"]) == money(250)
    assert money(body["details"]["order_amount"]) == money(200)


def test_update_returns_old_stock_and_reserves_new(api, db, seller, buyer, make_product):
    a = make_product(seller, stock=10, price=10)
    b = make_product(seller, stock=10, price=20)
    order = api.post("/orders", buyer.token, json=order_body((a, 3))).json()
    assert stock(db, a) == 7

    updated = api.put(f"/orders/{order['id']}", buyer.token, json=order_body((a, 10), (b, 1)))
    assert updated.status_code == 200
    body = updated.json()
    assert (stock(db, a), stock(db, b)) == (0, 9)
    assert money(body["total_amount"]) == money(120)
    assert body["updated_at"] > order["updated_at"]
    assert db.one("SELECT count(*) AS n FROM order_items WHERE order_id = %s", order["id"])["n"] == 2
    ops = db.all("SELECT operation_type FROM user_operations WHERE user_id = %s ORDER BY created_at", buyer.id)
    assert [op["operation_type"] for op in ops] == ["CREATE_ORDER", "UPDATE_ORDER"]


def test_update_takes_new_price_snapshot(api, seller, buyer, make_product):
    product = make_product(seller, price=10, stock=10)
    order = api.post("/orders", buyer.token, json=order_body((product, 1))).json()
    api.put(
        f"/products/{product['id']}", seller.token,
        json={"name": "x", "price": 15, "stock": 9, "category": "c", "status": "ACTIVE"},
    )
    updated = api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 2))).json()
    assert money(updated["items"][0]["price_at_order"]) == money(15)
    assert money(updated["total_amount"]) == money(30)


def test_failed_update_changes_nothing(api, db, seller, buyer, make_product):
    a = make_product(seller, stock=5)
    b = make_product(seller, stock=1)
    order = api.post("/orders", buyer.token, json=order_body((a, 2))).json()
    response = api.put(f"/orders/{order['id']}", buyer.token, json=order_body((a, 1), (b, 2)))
    body = assert_error(response, 409, "INSUFFICIENT_STOCK")
    assert body["details"]["items"] == [{"product_id": b["id"], "requested": 2, "available": 1}]
    assert (stock(db, a), stock(db, b)) == (3, 1)
    assert api.get(f"/orders/{order['id']}", buyer.token).json()["items"][0]["quantity"] == 2


def test_update_checks_owner_state_and_rate_limit_in_order(api, db, seller, buyer, admin, register, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 1))).json()
    stranger = register("USER")

    assert_error(api.put(f"/orders/{order['id']}", stranger.token, json=order_body((product, 2))), 403, "ORDER_OWNERSHIP_VIOLATION")
    assert api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 2))).status_code == 200
    assert_error(api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 3))), 429, "ORDER_LIMIT_EXCEEDED")
    api.patch(f"/orders/{order['id']}/status", admin.token, json={"status": "PAYMENT_PENDING"})
    body = assert_error(api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 3))), 409, "INVALID_STATE_TRANSITION")
    assert body["details"]["current_status"] == "PAYMENT_PENDING"


def test_update_unknown_order(api, buyer, seller, make_product):
    product = make_product(seller)
    assert_error(api.put(f"/orders/{uuid.uuid4()}", buyer.token, json=order_body((product, 1))), 404, "ORDER_NOT_FOUND")


def test_update_drops_promo_below_min_amount(api, db, seller, buyer, make_product, make_promo):
    product = make_product(seller, price=100, stock=10)
    promo = make_promo(discount_type="FIXED_AMOUNT", discount_value=50, min_order_amount=300)
    order = api.post("/orders", buyer.token, json=order_body((product, 3), promo_code=promo["code"])).json()
    assert money(order["total_amount"]) == money(250)
    assert promo_uses(db, promo) == 1

    updated = api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 2))).json()
    assert updated["promo_code"] is None
    assert money(updated["discount_amount"]) == 0
    assert money(updated["total_amount"]) == money(200)
    assert promo_uses(db, promo) == 0
    assert db.one("SELECT promo_code_id FROM orders WHERE id = %s", order["id"])["promo_code_id"] is None


def test_update_recalculates_promo(api, db, seller, buyer, make_product, make_promo):
    product = make_product(seller, price=100, stock=10)
    promo = make_promo(discount_type="PERCENTAGE", discount_value=10, min_order_amount=100)
    order = api.post("/orders", buyer.token, json=order_body((product, 1), promo_code=promo["code"])).json()
    updated = api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 5))).json()
    assert updated["promo_code"] == promo["code"]
    assert money(updated["discount_amount"]) == money(50)
    assert money(updated["total_amount"]) == money(450)
    assert promo_uses(db, promo) == 1


def test_update_drops_deactivated_promo(api, db, seller, buyer, make_product, make_promo):
    product = make_product(seller, price=100, stock=10)
    promo = make_promo(max_uses=1)
    order = api.post("/orders", buyer.token, json=order_body((product, 1), promo_code=promo["code"])).json()
    kept = api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 2))).json()
    assert kept["promo_code"] == promo["code"]

    db.execute("UPDATE promo_codes SET active = false WHERE id = %s", promo["id"])
    db.forget_rate_limit(buyer.id)
    dropped = api.put(f"/orders/{order['id']}", buyer.token, json=order_body((product, 2))).json()
    assert dropped["promo_code"] is None
    assert promo_uses(db, promo) == 0


def test_cancel_returns_stock_and_promo_use(api, db, seller, buyer, make_product, make_promo):
    product = make_product(seller, stock=10)
    promo = make_promo()
    order = api.post("/orders", buyer.token, json=order_body((product, 4), promo_code=promo["code"])).json()
    assert (stock(db, product), promo_uses(db, promo)) == (6, 1)

    canceled = api.post(f"/orders/{order['id']}/cancel", buyer.token)
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "CANCELED"
    assert (stock(db, product), promo_uses(db, promo)) == (10, 0)
    assert db.one("SELECT status FROM orders WHERE id = %s", order["id"])["status"] == "CANCELED"

    assert_error(api.post(f"/orders/{order['id']}/cancel", buyer.token), 409, "INVALID_STATE_TRANSITION")
    assert stock(db, product) == 10


def test_cancel_from_payment_pending(api, db, seller, buyer, admin, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 2))).json()
    api.patch(f"/orders/{order['id']}/status", admin.token, json={"status": "PAYMENT_PENDING"})
    assert api.post(f"/orders/{order['id']}/cancel", buyer.token).json()["status"] == "CANCELED"
    assert stock(db, product) == 10


def test_cannot_cancel_paid_order(api, db, seller, buyer, admin, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 2))).json()
    for status in ("PAYMENT_PENDING", "PAID"):
        api.patch(f"/orders/{order['id']}/status", admin.token, json={"status": status})
    body = assert_error(api.post(f"/orders/{order['id']}/cancel", buyer.token), 409, "INVALID_STATE_TRANSITION")
    assert body["details"] == {"current_status": "PAID", "target_status": "CANCELED"}
    assert_error(api.post(f"/orders/{order['id']}/cancel", admin.token), 409, "INVALID_STATE_TRANSITION")
    assert stock(db, product) == 8


def test_cancel_foreign_order(api, seller, buyer, register, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 1))).json()
    assert_error(api.post(f"/orders/{order['id']}/cancel", register("USER").token), 403, "ORDER_OWNERSHIP_VIOLATION")
    assert_error(api.post(f"/orders/{uuid.uuid4()}/cancel", buyer.token), 404, "ORDER_NOT_FOUND")


def test_full_lifecycle(api, seller, buyer, admin, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 1))).json()
    path = f"/orders/{order['id']}/status"

    assert_error(api.patch(path, buyer.token, json={"status": "PAYMENT_PENDING"}), 403, "ACCESS_DENIED")
    assert_error(api.patch(path, admin.token, json={"status": "PAID"}), 409, "INVALID_STATE_TRANSITION")
    assert api.patch(path, admin.token, json={"status": "PAYMENT_PENDING"}).json()["status"] == "PAYMENT_PENDING"
    assert api.patch(path, admin.token, json={"status": "PAID"}).json()["status"] == "PAID"
    assert_error(api.patch(path, admin.token, json={"status": "COMPLETED"}), 409, "INVALID_STATE_TRANSITION")
    assert api.patch(path, admin.token, json={"status": "SHIPPED"}).json()["status"] == "SHIPPED"
    assert api.patch(path, admin.token, json={"status": "COMPLETED"}).json()["status"] == "COMPLETED"
    for status in ("CREATED", "PAYMENT_PENDING", "PAID", "SHIPPED", "CANCELED"):
        assert_error(api.patch(path, admin.token, json={"status": status}), 409, "INVALID_STATE_TRANSITION")


def test_cancel_via_status_endpoint(api, db, seller, buyer, admin, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 3))).json()
    assert api.patch(f"/orders/{order['id']}/status", admin.token, json={"status": "CANCELED"}).json()["status"] == "CANCELED"
    assert stock(db, product) == 10


def test_get_order(api, seller, buyer, register, admin, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 1))).json()

    assert api.get(f"/orders/{order['id']}", buyer.token).json()["id"] == order["id"]
    assert api.get(f"/orders/{order['id']}", admin.token).json()["id"] == order["id"]
    assert_error(api.get(f"/orders/{order['id']}", register("USER").token), 403, "ORDER_OWNERSHIP_VIOLATION")
    assert_error(api.get(f"/orders/{uuid.uuid4()}", buyer.token), 404, "ORDER_NOT_FOUND")


def test_seller_cannot_use_orders(api, seller, buyer, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 1))).json()
    assert_error(api.post("/orders", seller.token, json=order_body((product, 1))), 403, "ACCESS_DENIED")
    assert_error(api.get(f"/orders/{order['id']}", seller.token), 403, "ACCESS_DENIED")
    assert_error(api.put(f"/orders/{order['id']}", seller.token, json=order_body((product, 1))), 403, "ACCESS_DENIED")
    assert_error(api.post(f"/orders/{order['id']}/cancel", seller.token), 403, "ACCESS_DENIED")


def test_admin_manages_any_order(api, db, seller, buyer, admin, make_product):
    product = make_product(seller)
    order = api.post("/orders", buyer.token, json=order_body((product, 1))).json()
    updated = api.put(f"/orders/{order['id']}", admin.token, json=order_body((product, 2))).json()
    assert updated["user_id"] == buyer.id
    assert api.post(f"/orders/{order['id']}/cancel", admin.token).json()["status"] == "CANCELED"
    assert stock(db, product) == 10
