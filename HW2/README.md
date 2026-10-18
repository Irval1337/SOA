# HW2

## Запуск
```bash
docker compose up --build
```

API: http://localhost:8000, Swagger: http://localhost:8000/docs, Postgres: localhost:5433

Админ: `admin@example.com`:`AdminPassw0rd`. Через регистрацию можно получить только USER или SELLER.

## Команды
```bash
make test # запустить тесты от ллм
make generate # сгенерировать код из openapi.yaml
make down # остановить и удалить данные
```

Интервал для лимита на создание/обновление заказа задается через `ORDER_RATE_LIMIT_MINUTES` (по умолчанию 1 минута). Спецификация лежит в `marketplace/openapi/openapi.yaml`. Из нее через fastapi-code-generator со своими шаблонами (`marketplace/codegen`) генерируются pydantic модели и роутеры с интерфейсами сервисов.

Миграции делает Flyway отдельным контейнером перед стартом приложения.

Роли для каждой операции указаны в спеке в `x-roles`, по ним генерируется проверка роли. А проверка владельца (наприме свой товар, свой заказ) сделана в сервисах.
