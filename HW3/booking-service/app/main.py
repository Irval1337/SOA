import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

import asyncpg
import grpc
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.flight_client import FlightClient, create_channel
from app.resilience import CircuitBreaker, CircuitOpenError
from app.schemas import BookingCreate, BookingOut, ErrorOut, FlightId, FlightOut, IataCode
from app.service import ApiError, BookingService

log = logging.getLogger(__name__)

_GRPC_TO_HTTP = {
    grpc.StatusCode.INVALID_ARGUMENT: (400, "INVALID_ARGUMENT"),
    grpc.StatusCode.NOT_FOUND: (404, "NOT_FOUND"),
    grpc.StatusCode.RESOURCE_EXHAUSTED: (409, "NOT_ENOUGH_SEATS"),
    grpc.StatusCode.FAILED_PRECONDITION: (409, "FLIGHT_NOT_AVAILABLE"),
    grpc.StatusCode.ALREADY_EXISTS: (409, "RESERVATION_CONFLICT"),
    grpc.StatusCode.UNAVAILABLE: (503, "FLIGHT_SERVICE_UNAVAILABLE"),
    grpc.StatusCode.DEADLINE_EXCEEDED: (504, "FLIGHT_SERVICE_TIMEOUT"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    breaker = CircuitBreaker(
        settings.cb_failure_threshold, settings.cb_window_seconds, settings.cb_open_timeout_seconds
    )
    channel = create_channel(settings, breaker)
    compensation_channel = create_channel(settings, breaker=None)
    flights = FlightClient(
        channel, compensation_channel, settings.flight_service_api_key, settings.flight_rpc_timeout_seconds
    )
    app.state.pool = pool
    app.state.flights = flights
    app.state.bookings = BookingService(pool, flights)
    yield
    await channel.close()
    await compensation_channel.close()
    await pool.close()


app = FastAPI(title="Booking Service", lifespan=lifespan, responses={"default": {"model": ErrorOut}})


def _error(status_code: int, error: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": error, "message": message}, status_code=status_code, headers=headers)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return _error(exc.status_code, exc.error, exc.message)


@app.exception_handler(grpc.aio.AioRpcError)
async def grpc_error_handler(request: Request, exc: grpc.aio.AioRpcError):
    status_code, error = _GRPC_TO_HTTP.get(exc.code(), (502, "FLIGHT_SERVICE_ERROR"))
    if status_code >= 500:
        log.error("flight service call failed: %s %s", exc.code().name, exc.details())
    return _error(status_code, error, exc.details() or exc.code().name)


@app.exception_handler(CircuitOpenError)
async def circuit_open_handler(request: Request, exc: CircuitOpenError):
    retry_after = max(1, round(exc.retry_after_seconds))
    return _error(
        503,
        "FLIGHT_SERVICE_UNAVAILABLE",
        "flight service is temporarily unavailable, try again later",
        headers={"Retry-After": str(retry_after)},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    return _error(500, "INTERNAL_ERROR", "internal server error")


@app.get("/health")
async def health():
    async with app.state.pool.acquire() as conn:
        await conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/flights", response_model=list[FlightOut])
async def search_flights(
    origin: IataCode, destination: IataCode, departure_date: Annotated[date | None, Query(alias="date")] = None
):
    flights = await app.state.flights.search_flights(origin, destination, departure_date)
    return [FlightOut.from_proto(flight) for flight in flights]


@app.get("/flights/{flight_id}", response_model=FlightOut)
async def get_flight(flight_id: FlightId):
    return FlightOut.from_proto(await app.state.flights.get_flight(flight_id))


@app.post("/bookings", response_model=BookingOut, status_code=201)
async def create_booking(data: BookingCreate):
    return dict(await app.state.bookings.create(data))


@app.get("/bookings", response_model=list[BookingOut])
async def list_bookings(user_id: Annotated[str, Query(min_length=1, max_length=64)]):
    return [dict(row) for row in await app.state.bookings.list_for_user(user_id)]


@app.get("/bookings/{booking_id}", response_model=BookingOut)
async def get_booking(booking_id: UUID):
    return dict(await app.state.bookings.get(booking_id))


@app.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
async def cancel_booking(booking_id: UUID):
    return dict(await app.state.bookings.cancel(booking_id))
