CREATE TYPE discount_type AS ENUM ('PERCENTAGE', 'FIXED_AMOUNT');

CREATE TABLE promo_codes
(
    id               UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
    code             VARCHAR(20)    NOT NULL,
    discount_type    discount_type  NOT NULL,
    discount_value   NUMERIC(12, 2) NOT NULL,
    min_order_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    max_uses         INTEGER        NOT NULL,
    current_uses     INTEGER        NOT NULL DEFAULT 0,
    valid_from       TIMESTAMPTZ    NOT NULL,
    valid_until      TIMESTAMPTZ    NOT NULL,
    active           BOOLEAN        NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT ux_promo_codes_code UNIQUE (code),
    CONSTRAINT ck_promo_codes_discount_value CHECK (
        discount_value > 0 AND (discount_type <> 'PERCENTAGE' OR discount_value <= 100)
    ),
    CONSTRAINT ck_promo_codes_min_order_amount CHECK (min_order_amount >= 0),
    CONSTRAINT ck_promo_codes_uses CHECK (max_uses > 0 AND current_uses >= 0 AND current_uses <= max_uses),
    CONSTRAINT ck_promo_codes_validity CHECK (valid_until > valid_from)
);
