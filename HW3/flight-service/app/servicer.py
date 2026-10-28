import logging
import re
from datetime import date, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from app import repository
from app.cache import Cache, flight_key, search_key
from flight.v1 import flight_pb2 as pb
from flight.v1 import flight_pb2_grpc as pb_grpc

log = logging.getLogger(__name__)

IATA_AIRPORT = re.compile(r"^[A-Z]{3}$")
MAX_PRICE_KOPECKS = 10**10 - 1


def _timestamp(value) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(value)
    return ts


def _to_flight(row: asyncpg.Record) -> pb.Flight:
    return pb.Flight(
        id=row["id"],
        flight_number=row["flight_number"],
        airline=pb.Airline(iata_code=row["airline_code"], name=row["airline_name"]),
        origin=row["origin"],
        destination=row["destination"],
        departure_time=_timestamp(row["departure_time"]),
        arrival_time=_timestamp(row["arrival_time"]),
        total_seats=row["total_seats"],
        available_seats=row["available_seats"],
        price_kopecks=int(row["price"] * 100),
        status=pb.FlightStatus.Value(f"FLIGHT_STATUS_{row['status']}"),
    )


def _to_reservation(row: asyncpg.Record) -> pb.SeatReservation:
    return pb.SeatReservation(
        id=row["id"],
        flight_id=row["flight_id"],
        booking_id=str(row["booking_id"]),
        seat_count=row["seat_count"],
        status=pb.ReservationStatus.Value(f"RESERVATION_STATUS_{row['status']}"),
        created_at=_timestamp(row["created_at"]),
    )


def _to_date(ts: Timestamp) -> date | None:
    try:
        day = ts.ToDatetime(tzinfo=timezone.utc).date()
    except ValueError:
        return None
    return day if day < date.max else None


async def _parse_booking_id(value: str, context: grpc.aio.ServicerContext) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "booking_id must be a UUID")


class FlightServicer(pb_grpc.FlightServiceServicer):
    def __init__(self, pool: asyncpg.Pool, cache: Cache):
        self._pool = pool
        self._cache = cache

    async def SearchFlights(self, request, context):
        if not (IATA_AIRPORT.match(request.origin) and IATA_AIRPORT.match(request.destination)):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "origin and destination must be IATA codes")
        departure_date = None
        if request.HasField("departure_date"):
            departure_date = _to_date(request.departure_date)
            if departure_date is None:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "departure_date is out of range")

        key = search_key(request.origin, request.destination, departure_date)
        cached, version = await self._cache.get(key, pb.SearchFlightsResponse)
        if cached is not None:
            return cached
        async with self._pool.acquire() as conn:
            rows = await repository.search_flights(conn, request.origin, request.destination, departure_date)
        response = pb.SearchFlightsResponse(flights=[_to_flight(row) for row in rows])
        await self._cache.set(key, response, version)
        return response

    async def GetFlight(self, request, context):
        if request.flight_id <= 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "flight_id must be positive")

        key = flight_key(request.flight_id)
        cached, version = await self._cache.get(key, pb.Flight)
        if cached is not None:
            return cached
        async with self._pool.acquire() as conn:
            row = await repository.get_flight(conn, request.flight_id)
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"flight {request.flight_id} not found")
        flight = _to_flight(row)
        await self._cache.set(key, flight, version)
        return flight

    async def ReserveSeats(self, request, context):
        booking_id = await _parse_booking_id(request.booking_id, context)
        if request.flight_id <= 0 or request.seat_count <= 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "flight_id and seat_count must be positive")

        try:
            async with self._pool.acquire() as conn, conn.transaction():
                flight = await repository.lock_flight(conn, request.flight_id)
                if flight is None:
                    await context.abort(grpc.StatusCode.NOT_FOUND, f"flight {request.flight_id} not found")

                existing = await repository.get_reservation(conn, booking_id)
                if existing is not None:
                    if (
                        existing["flight_id"] == request.flight_id
                        and existing["seat_count"] == request.seat_count
                        and existing["status"] == "ACTIVE"
                    ):
                        log.info("booking %s already has reservation %s, returning it", booking_id, existing["id"])
                        return _to_reservation(existing)
                    await context.abort(
                        grpc.StatusCode.ALREADY_EXISTS, f"booking {booking_id} already has another reservation"
                    )

                if flight["status"] != "SCHEDULED":
                    await context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION, f"flight {request.flight_id} is {flight['status']}"
                    )
                if flight["available_seats"] < request.seat_count:
                    await context.abort(
                        grpc.StatusCode.RESOURCE_EXHAUSTED,
                        f"requested {request.seat_count} seats, available {flight['available_seats']}",
                    )

                await repository.change_available_seats(conn, request.flight_id, -request.seat_count)
                reservation = await repository.insert_reservation(
                    conn, request.flight_id, booking_id, request.seat_count
                )
        except asyncpg.UniqueViolationError:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, f"booking {booking_id} already has a reservation")

        log.info(
            "reserved %d seats on flight %d for booking %s, %d left",
            request.seat_count,
            request.flight_id,
            booking_id,
            flight["available_seats"] - request.seat_count,
        )
        await self._invalidate(flight)
        return _to_reservation(reservation)

    async def ReleaseReservation(self, request, context):
        booking_id = await _parse_booking_id(request.booking_id, context)

        async with self._pool.acquire() as conn, conn.transaction():
            reservation = await repository.get_reservation(conn, booking_id, for_update=True)
            if reservation is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, f"reservation for booking {booking_id} not found")
            if reservation["status"] != "ACTIVE":
                return _to_reservation(reservation)

            flight = await repository.lock_flight(conn, reservation["flight_id"])
            await repository.change_available_seats(conn, reservation["flight_id"], reservation["seat_count"])
            reservation = await repository.set_reservation_status(conn, reservation["id"], "RELEASED")

        log.info(
            "released %d seats on flight %d for booking %s",
            reservation["seat_count"],
            reservation["flight_id"],
            booking_id,
        )
        await self._invalidate(flight)
        return _to_reservation(reservation)

    async def UpdateFlight(self, request, context):
        if request.flight_id <= 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "flight_id must be positive")
        price = status = None
        if request.HasField("price_kopecks"):
            if not 0 < request.price_kopecks <= MAX_PRICE_KOPECKS:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, f"price_kopecks must be in 1..{MAX_PRICE_KOPECKS}"
                )
            price = Decimal(request.price_kopecks) / 100
        if request.HasField("status"):
            if request.status == pb.FLIGHT_STATUS_UNSPECIFIED or request.status not in pb.FlightStatus.values():
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "unknown flight status")
            status = pb.FlightStatus.Name(request.status).removeprefix("FLIGHT_STATUS_")

        async with self._pool.acquire() as conn:
            updated = await repository.update_flight(conn, request.flight_id, price, status)
            if updated is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, f"flight {request.flight_id} not found")
            row = await repository.get_flight(conn, request.flight_id)

        log.info("updated flight %d: price=%s status=%s", request.flight_id, price, status)
        await self._invalidate(updated)
        return _to_flight(row)

    async def _invalidate(self, flight: asyncpg.Record) -> None:
        departure_date = flight["departure_time"].astimezone(timezone.utc).date()
        await self._cache.invalidate(
            flight_key(flight["id"]),
            search_key(flight["origin"], flight["destination"], departure_date),
            search_key(flight["origin"], flight["destination"], None),
        )
