CREATE TYPE operation_type AS ENUM ('CREATE_ORDER', 'UPDATE_ORDER');

CREATE TABLE user_operations
(
    id             UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
    user_id        UUID           NOT NULL REFERENCES users (id),
    operation_type operation_type NOT NULL,
    created_at     TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX ix_user_operations_user_type_created_at
    ON user_operations (user_id, operation_type, created_at DESC);
