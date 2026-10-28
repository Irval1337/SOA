from datetime import date, datetime, time, timezone
from uuid import UUID

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from app.config import Settings
from app.resilience import CircuitBreaker, CircuitBreakerInterceptor, RetryInterceptor
from flight.v1 import flight_pb2 as pb
from flight.v1 import flight_pb2_grpc as pb_grpc


def create_channel(settings: Settings, breaker: CircuitBreaker | None) -> grpc.aio.Channel:
    interceptors = [RetryInterceptor(settings.retry_max_attempts, settings.retry_base_delay_seconds)]
    if breaker is not None:
        interceptors.insert(0, CircuitBreakerInterceptor(breaker))
    return grpc.aio.insecure_channel(
        settings.flight_service_addr,
        interceptors=interceptors,
        options=[("grpc.initial_reconnect_backoff_ms", 500), ("grpc.max_reconnect_backoff_ms", 3000)],
    )


class FlightClient:
    def __init__(
        self,
        channel: grpc.aio.Channel,
        compensation_channel: grpc.aio.Channel,
        api_key: str,
        timeout_seconds: float,
    ):
        self._stub = pb_grpc.FlightServiceStub(channel)
        self._compensation_stub = pb_grpc.FlightServiceStub(compensation_channel)
        self._metadata = (("x-api-key", api_key),)
        self._timeout = timeout_seconds

    async def search_flights(self, origin: str, destination: str, departure_date: date | None) -> list[pb.Flight]:
        request = pb.SearchFlightsRequest(origin=origin, destination=destination)
        if departure_date is not None:
            day_start = Timestamp()
            day_start.FromDatetime(datetime.combine(departure_date, time.min, tzinfo=timezone.utc))
            request.departure_date.CopyFrom(day_start)
        response = await self._stub.SearchFlights(request, metadata=self._metadata, timeout=self._timeout)
        return list(response.flights)

    async def get_flight(self, flight_id: int) -> pb.Flight:
        request = pb.GetFlightRequest(flight_id=flight_id)
        return await self._stub.GetFlight(request, metadata=self._metadata, timeout=self._timeout)

    async def reserve_seats(self, flight_id: int, seat_count: int, booking_id: UUID) -> pb.SeatReservation:
        request = pb.ReserveSeatsRequest(flight_id=flight_id, seat_count=seat_count, booking_id=str(booking_id))
        return await self._stub.ReserveSeats(request, metadata=self._metadata, timeout=self._timeout)

    async def release_reservation(self, booking_id: UUID) -> pb.SeatReservation:
        request = pb.ReleaseReservationRequest(booking_id=str(booking_id))
        return await self._stub.ReleaseReservation(request, metadata=self._metadata, timeout=self._timeout)

    async def compensate_reservation(self, booking_id: UUID) -> pb.SeatReservation:
        request = pb.ReleaseReservationRequest(booking_id=str(booking_id))
        return await self._compensation_stub.ReleaseReservation(request, metadata=self._metadata, timeout=self._timeout)
