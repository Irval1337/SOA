# HW5

## Запуск

```bash
docker compose up
```

Movie Service — http://localhost:8051/docs, агрегация — http://localhost:8052/docs, ClickHouse HTTP — http://localhost:8123, S3 — http://localhost:8333. [Дашборд Grafana](http://localhost:3050/d/online-cinema) открывается без входа. Демоданные появятся после первого пересчёта.

## Команды

```bash
./tests/run.sh
docker compose logs -f movie-service aggregation
docker compose down
```

Тесты запускаются в отдельном Compose-проекте. Запросы для защиты лежат в [http/](http/). Выполнять по порядку. Команды для проверки PostgreSQL, чтения файла из S3 и остановки брокера указаны в комментариях.

- [1-events.http](http/1-events.http) — валидация, публикация и проверка событий в ClickHouse.
- [2-analytics.http](http/2-analytics.http) — пересчёт, метрики, Grafana и повторный экспорт.
- [3-kafka-failover.http](http/3-kafka-failover.http) — публикация и чтение при остановленном брокере.

## Как устроено

Compose регистрирует [Avro-схему v1](schema/movie-event-v1.avsc) в Schema Registry и применяет миграции при старте. У `movie-events` три партиции, по три реплики и `min.insync.replicas=2`. Ключ `user_id` сохраняет порядок событий одного пользователя при переключении между фильмами. Продюсер ждёт `acks=all` и повторяет запись с экспоненциальной задержкой.

Kafka Engine и Materialized View пишут события в `cinema.raw_events`. Агрегаты считаются за сутки UTC. Рейтинг строится по стартам, среднее время — по завершениям, конверсия — это число завершений, делённое на число стартов. Retention показывает долю когорты, которая вернулась к просмотру через N дней после первого просмотра. Будущие дни остаются пустыми.

Агрегация записывает последние версии метрик в ClickHouse и обновляет строки в PostgreSQL по ключу `(day, metric, dimension)`. Экспорт сохраняет метрики из PostgreSQL в `s3://movie-analytics/daily/YYYY-MM-DD/aggregates.json`. Неудачная выгрузка повторяется в следующем цикле.

`AGGREGATION_INTERVAL_SECONDS` задаёт интервал пересчёта (по умолчанию 60 секунд), `AGGREGATION_LOOKBACK_DAYS` — число предыдущих суток (14). Для отключения демоданных установить `DEMO_HISTORY_DAYS=0` и `GENERATOR_INTERVAL_SECONDS=0`.
