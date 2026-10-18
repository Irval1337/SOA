CREATE TYPE product_status AS ENUM ('ACTIVE', 'INACTIVE', 'ARCHIVED');

CREATE TABLE products
(
    id          UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
    name        VARCHAR(255)   NOT NULL,
    description VARCHAR(4000),
    price       NUMERIC(12, 2) NOT NULL,
    stock       INTEGER        NOT NULL,
    category    VARCHAR(100)   NOT NULL,
    status      product_status NOT NULL DEFAULT 'ACTIVE',
    seller_id   UUID           NOT NULL REFERENCES users (id),
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT ck_products_price_positive CHECK (price > 0),
    CONSTRAINT ck_products_stock_non_negative CHECK (stock >= 0)
);

CREATE INDEX ix_products_status ON products (status);

CREATE INDEX ix_products_category ON products (category);

CREATE INDEX ix_products_created_at ON products (created_at DESC, id DESC);

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
