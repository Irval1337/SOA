from decimal import Decimal
from uuid import UUID

import asyncpg

_COLUMNS = (
    "id, user_id, flight_id, passenger_name, passenger_email, seat_count, total_price, status, created_at, updated_at"
)


async def insert_booking(
    conn: asyncpg.Connection,
    booking_id: UUID,
    user_id: str,
    flight_id: int,
    passenger_name: str,
    passenger_email: str,
    seat_count: int,
    total_price: Decimal,
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""
        INSERT INTO bookings (id, user_id, flight_id, passenger_name, passenger_email, seat_count, total_price)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_COLUMNS}
        """,
        booking_id,
        user_id,
        flight_id,
        passenger_name,
        passenger_email,
        seat_count,
        total_price,
    )


async def get_booking(conn: asyncpg.Connection, booking_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(f"SELECT {_COLUMNS} FROM bookings WHERE id = $1", booking_id)


async def list_user_bookings(conn: asyncpg.Connection, user_id: str) -> list[asyncpg.Record]:
    return await conn.fetch(f"SELECT {_COLUMNS} FROM bookings WHERE user_id = $1 ORDER BY created_at DESC", user_id)


async def cancel_booking(conn: asyncpg.Connection, booking_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"""
        UPDATE bookings SET status = 'CANCELLED', updated_at = now()
        WHERE id = $1 AND status = 'CONFIRMED'
        RETURNING {_COLUMNS}
        """,
        booking_id,
    )
