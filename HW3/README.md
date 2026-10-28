# HW3

## Запуск
```bash
docker compose up --build
```

Booking Service: http://localhost:8081, Swagger: http://localhost:8081/docs. Flight Service (gRPC): localhost:50051, ключ в metadata `x-api-key: dev-flight-service-key`. Postgres рейсов на localhost:5434, бронирований на localhost:5435.

## Команды
```bash
make test # тесты (стек должен быть запущен, тесты кеша ходят в редис)
make race # 10 параллельных броней на рейс 6, где всего 3 места
make grpc METHOD=GetFlight DATA='{"flight_id": 1}' # дернуть flight service напрямую через grpcurl
make redis-keys # ключи в кеше и их ttl
make redis-master # кто сейчас мастер у sentinel
make proto # сгенерировать код из proto локально, для IDE
make down # остановить и удалить данные
```

Контракт лежит в `proto/flight/v1/flight.proto`, код из него генерируется protoc при сборке образов. ER-диаграмма в `docs/er.md`. Миграции делает Flyway, отдельный контейнер на каждую базу. Тестовые рейсы добавляются миграцией `V2__seed_flights.sql`. Запросы для проверки лежат в `http/`, выполнять по порядку файлов. В 3 и 4 между запросами есть команды для терминала, они написаны в комментариях.

Booking ходит во Flight Service через два интерцептора: circuit breaker и retry. Параметры задаются через env (`RETRY_*`, `CB_*`). Возврат мест при неудачном бронировании идет через отдельный канал без breaker'а, иначе он мог бы сам себя заблокировать.

Redis поднят с sentinel (мастер, реплика, 3 sentinel). У нод статические IP, а `SENTINEL_TILT_PERIOD_MS` сокращает TILT: у меня в WSL часы периодически прыгают назад и без этого failover не происходил.
