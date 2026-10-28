# ER-диаграмма

```mermaid
erDiagram
    airlines ||--o{ flights : ""
    flights ||--o{ seat_reservations : ""
    flights ||..o{ bookings : "другая БД"
    bookings ||..|| seat_reservations : "другая БД"

    airlines {
        char(2) iata_code PK
        varchar name
    }

    flights {
        bigint id PK
        char(2) airline_code FK
        varchar number "unique с airline_code и датой вылета"
        char(3) origin "IATA"
        char(3) destination "IATA, <> origin"
        timestamptz departure_time
        timestamptz arrival_time "> departure_time"
        int total_seats "> 0"
        int available_seats ">= 0, <= total_seats"
        numeric price "> 0"
        varchar status "SCHEDULED, DEPARTED, CANCELLED, COMPLETED"
        timestamptz created_at
        timestamptz updated_at
    }

    seat_reservations {
        bigint id PK
        bigint flight_id FK
        uuid booking_id UK
        int seat_count "> 0"
        varchar status "ACTIVE, RELEASED, EXPIRED"
        timestamptz created_at
        timestamptz updated_at
    }

    bookings {
        uuid id PK
        varchar user_id
        bigint flight_id
        varchar passenger_name
        varchar passenger_email
        int seat_count "> 0"
        numeric total_price "> 0"
        varchar status "CONFIRMED, CANCELLED"
        timestamptz created_at
        timestamptz updated_at
    }
```

`airlines`, `flights` и `seat_reservations` лежат в базе Flight Service, `bookings` в базе Booking Service. Поэтому `bookings.flight_id` и `seat_reservations.booking_id` без внешних ключей.

Авиакомпания вынесена в отдельную таблицу, потому что название зависит от кода, а не от рейса. По той же причине номер рейса хранится как код авиакомпании + число (`SU` + `1234`), `SU1234` собирается в запросе. Дата вылета отдельно не хранится, уникальность рейса на дату сделана индексом по `departure_time`.

`available_seats` можно посчитать по активным резервациям, но ТЗ требует хранить это поле. Оно меняется в той же транзакции, что и резервации, так что не расходится. В `bookings` хранится только `total_price`, это цена на момент брони, а не текущая цена рейса.

Все ограничения сделаны через CHECK в миграциях: `flight-service/migrations` и `booking-service/migrations`.
