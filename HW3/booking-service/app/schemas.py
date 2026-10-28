from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, PlainSerializer, StringConstraints

from flight.v1 import flight_pb2 as pb

IataCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
FlightId = Annotated[int, Field(gt=0, le=2**63 - 1)]
Money = Annotated[Decimal, PlainSerializer(float, return_type=float)]


def kopecks_to_rubles(kopecks: int) -> Decimal:
    return Decimal(kopecks).scaleb(-2)


class Airline(BaseModel):
    iata_code: str
    name: str


class FlightOut(BaseModel):
    id: int
    flight_number: str
    airline: Airline
    origin: str
    destination: str
    departure_time: datetime
    arrival_time: datetime
    total_seats: int
    available_seats: int
    price: Money
    status: str

    @classmethod
    def from_proto(cls, flight: pb.Flight) -> "FlightOut":
        return cls(
            id=flight.id,
            flight_number=flight.flight_number,
            airline=Airline(iata_code=flight.airline.iata_code, name=flight.airline.name),
            origin=flight.origin,
            destination=flight.destination,
            departure_time=flight.departure_time.ToDatetime(tzinfo=timezone.utc),
            arrival_time=flight.arrival_time.ToDatetime(tzinfo=timezone.utc),
            total_seats=flight.total_seats,
            available_seats=flight.available_seats,
            price=kopecks_to_rubles(flight.price_kopecks),
            status=pb.FlightStatus.Name(flight.status).removeprefix("FLIGHT_STATUS_"),
        )


class BookingCreate(BaseModel):
    user_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    flight_id: FlightId
    passenger_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    passenger_email: EmailStr
    seat_count: int = Field(ge=1, le=2**31 - 1)


class BookingOut(BaseModel):
    id: UUID
    user_id: str
    flight_id: int
    passenger_name: str
    passenger_email: str
    seat_count: int
    total_price: Money
    status: str
    created_at: datetime
    updated_at: datetime


class ErrorOut(BaseModel):
    error: str
    message: str
