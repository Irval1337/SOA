from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg

_FLIGHT_SELECT = """
    SELECT f.id, f.airline_code, a.name AS airline_name, f.airline_code || f.number AS flight_number,
           f.origin, f.destination, f.departure_time, f.arrival_time,
           f.total_seats, f.available_seats, f.price, f.status
    FROM flights f
    JOIN airlines a ON a.iata_code = f.airline_code
"""

_RESERVATION_COLUMNS = "id, flight_id, booking_id, seat_count, status, created_at"


async def search_flights(
    conn: asyncpg.Connection, origin: str, destination: str, departure_date: date | None
) -> list[asyncpg.Record]:
    query = _FLIGHT_SELECT + " WHERE f.origin = $1 AND f.destination = $2 AND f.status = 'SCHEDULED'"
    args: list = [origin, destination]
    if departure_date is not None:
        day_start = datetime.combine(departure_date, time.min, tzinfo=timezone.utc)
        query += " AND f.departure_time >= $3 AND f.departure_time < $4"
        args += [day_start, day_start + timedelta(days=1)]
    return await conn.fetch(query + " ORDER BY f.departure_time", *args)


async def get_flight(conn: asyncpg.Connection, flight_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(_FLIGHT_SELECT + " WHERE f.id = $1", flight_id)


async def lock_flight(conn: asyncpg.Connection, flight_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, origin, destination, departure_time, available_seats, status FROM flights WHERE id = $1 FOR UPDATE",
        flight_id,
    )


async def change_available_seats(conn: asyncpg.Connection, flight_id: int, delta: int) -> None:
    await conn.execute(
        "UPDATE flights SET available_seats = available_seats + $2, updated_at = now() WHERE id = $1",
        flight_id,
        delta,
    )


async def update_flight(
    conn: asyncpg.Connection, flight_id: int, price: Decimal | None, status: str | None
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE flights
        SET price = COALESCE($2, price), status = COALESCE($3, status), updated_at = now()
        WHERE id = $1
        RETURNING id, origin, destination, departure_time
        """,
        flight_id,
        price,
        status,
    )


async def get_reservation(
    conn: asyncpg.Connection, booking_id: UUID, *, for_update: bool = False
) -> asyncpg.Record | None:
    query = f"SELECT {_RESERVATION_COLUMNS} FROM seat_reservations WHERE booking_id = $1"
    return await conn.fetchrow(query + (" FOR UPDATE" if for_update else ""), booking_id)


async def insert_reservation(
    conn: asyncpg.Connection, flight_id: int, booking_id: UUID, seat_count: int
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""
        INSERT INTO seat_reservations (flight_id, booking_id, seat_count)
        VALUES ($1, $2, $3)
        RETURNING {_RESERVATION_COLUMNS}
        """,
        flight_id,
        booking_id,
        seat_count,
    )


async def set_reservation_status(conn: asyncpg.Connection, reservation_id: int, status: str) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""
        UPDATE seat_reservations SET status = $2, updated_at = now()
        WHERE id = $1
        RETURNING {_RESERVATION_COLUMNS}
        """,
        reservation_id,
        status,
    )
