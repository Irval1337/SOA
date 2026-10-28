import logging
from uuid import UUID, uuid4

import asyncpg
import grpc

from app import repository
from app.flight_client import FlightClient
from app.schemas import BookingCreate, kopecks_to_rubles

log = logging.getLogger(__name__)

BUSINESS_ERRORS = {
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.FAILED_PRECONDITION,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.ALREADY_EXISTS,
}


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


class BookingService:
    def __init__(self, pool: asyncpg.Pool, flights: FlightClient):
        self._pool = pool
        self._flights = flights

    async def create(self, data: BookingCreate) -> asyncpg.Record:
        flight = await self._flights.get_flight(data.flight_id)
        booking_id = uuid4()
        try:
            await self._flights.reserve_seats(data.flight_id, data.seat_count, booking_id)
        except grpc.aio.AioRpcError as e:
            if e.code() not in BUSINESS_ERRORS:
                await self._release_quietly(booking_id)
            raise

        total_price = kopecks_to_rubles(flight.price_kopecks * data.seat_count)
        try:
            async with self._pool.acquire() as conn:
                booking = await repository.insert_booking(
                    conn,
                    booking_id,
                    data.user_id,
                    data.flight_id,
                    data.passenger_name,
                    data.passenger_email,
                    data.seat_count,
                    total_price,
                )
        except BaseException:
            await self._release_quietly(booking_id)
            raise

        log.info(
            "booking %s confirmed: flight %d, %d seats, %s", booking_id, data.flight_id, data.seat_count, total_price
        )
        return booking

    async def get(self, booking_id: UUID) -> asyncpg.Record:
        async with self._pool.acquire() as conn:
            booking = await repository.get_booking(conn, booking_id)
        if booking is None:
            raise ApiError(404, "BOOKING_NOT_FOUND", f"booking {booking_id} not found")
        return booking

    async def list_for_user(self, user_id: str) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await repository.list_user_bookings(conn, user_id)

    async def cancel(self, booking_id: UUID) -> asyncpg.Record:
        booking = await self.get(booking_id)
        if booking["status"] != "CONFIRMED":
            raise ApiError(409, "BOOKING_NOT_CONFIRMED", f"booking {booking_id} is {booking['status']}")

        await self._flights.release_reservation(booking_id)
        try:
            async with self._pool.acquire() as conn:
                cancelled = await repository.cancel_booking(conn, booking_id)
        except Exception:
            log.error("booking %s: seats released but status not updated, cancel must be repeated", booking_id)
            raise
        if cancelled is None:
            raise ApiError(409, "BOOKING_NOT_CONFIRMED", f"booking {booking_id} is already cancelled")

        log.info("booking %s cancelled", booking_id)
        return cancelled

    async def _release_quietly(self, booking_id: UUID) -> None:
        try:
            await self._flights.compensate_reservation(booking_id)
            log.warning("compensation: released reservation for booking %s", booking_id)
        except grpc.aio.AioRpcError as e:
            if e.code() != grpc.StatusCode.NOT_FOUND:
                log.error("compensation failed for booking %s: %s", booking_id, e.code().name)
        except Exception as e:
            log.error("compensation failed for booking %s: %r", booking_id, e)
