CREATE TYPE order_status AS ENUM ('CREATED', 'PAYMENT_PENDING', 'PAID', 'SHIPPED', 'COMPLETED', 'CANCELED');

CREATE TABLE orders
(
    id              UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
    user_id         UUID           NOT NULL REFERENCES users (id),
    status          order_status   NOT NULL DEFAULT 'CREATED',
    promo_code_id   UUID REFERENCES promo_codes (id),
    total_amount    NUMERIC(12, 2) NOT NULL,
    discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT ck_orders_total_amount CHECK (total_amount >= 0),
    CONSTRAINT ck_orders_discount_amount CHECK (discount_amount >= 0)
);

CREATE UNIQUE INDEX ux_orders_one_active_per_user
    ON orders (user_id)
    WHERE status IN ('CREATED', 'PAYMENT_PENDING');

CREATE TRIGGER trg_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE order_items
(
    id             UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
    order_id       UUID           NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id     UUID           NOT NULL REFERENCES products (id),
    quantity       INTEGER        NOT NULL,
    price_at_order NUMERIC(12, 2) NOT NULL,
    CONSTRAINT ux_order_items_order_product UNIQUE (order_id, product_id),
    CONSTRAINT ck_order_items_quantity CHECK (quantity BETWEEN 1 AND 999),
    CONSTRAINT ck_order_items_price CHECK (price_at_order > 0)
);

CREATE INDEX ix_order_items_product_id ON order_items (product_id);
