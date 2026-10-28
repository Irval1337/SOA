CREATE TABLE bookings (
    id              UUID PRIMARY KEY,
    user_id         VARCHAR(64)    NOT NULL CHECK (length(user_id) > 0),
    flight_id       BIGINT         NOT NULL CHECK (flight_id > 0),
    passenger_name  VARCHAR(200)   NOT NULL CHECK (length(trim(passenger_name)) > 0),
    passenger_email VARCHAR(254)   NOT NULL CHECK (passenger_email LIKE '%_@_%'),
    seat_count      INTEGER        NOT NULL CHECK (seat_count > 0),
    total_price     NUMERIC(12, 2) NOT NULL CHECK (total_price > 0),
    status          VARCHAR(16)    NOT NULL DEFAULT 'CONFIRMED' CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX bookings_user_idx ON bookings (user_id, created_at DESC);
