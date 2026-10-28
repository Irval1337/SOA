COMPOSE := docker compose
API_KEY ?= dev-flight-service-key
METHOD ?= GetFlight
DATA ?= {"flight_id": 1}

.PHONY: up up-d down logs test proto psql-flights psql-bookings redis-keys redis-master race grpc

up:
	$(COMPOSE) up --build

up-d:
	$(COMPOSE) up --build -d --wait

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f flight-service booking-service

test:
	$(COMPOSE) --profile test run --rm --build booking-tests
	$(COMPOSE) --profile test run --rm --build flight-tests

proto:
	for svc in flight-service booking-service; do \
		uv run --no-project --with grpcio-tools==1.85.0 python -m grpc_tools.protoc -I proto \
			--python_out=$$svc --pyi_out=$$svc --grpc_python_out=$$svc proto/flight/v1/flight.proto; \
	done

psql-flights:
	$(COMPOSE) exec flight-db psql -U flights -d flights

psql-bookings:
	$(COMPOSE) exec booking-db psql -U bookings -d bookings

redis-keys:
	@master=$$($(COMPOSE) exec -T redis-sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name mymaster | head -1); \
	echo "master: $$master"; \
	$(COMPOSE) exec -T redis-sentinel-1 sh -c "for key in \$$(redis-cli -h $$master --scan); do echo \$$key ttl=\$$(redis-cli -h $$master ttl \$$key); done"

redis-master:
	@for i in 1 2 3; do $(COMPOSE) exec -T redis-sentinel-$$i redis-cli -p 26379 sentinel get-master-addr-by-name mymaster | head -1; done

race:
	@seq 10 | xargs -P 10 -I{} curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8081/bookings \
		-H 'Content-Type: application/json' \
		-d '{"user_id":"race","flight_id":6,"passenger_name":"Racer {}","passenger_email":"racer{}@example.com","seat_count":1}' \
		| sort | uniq -c

grpc:
	@docker run --rm --network host -v "$(CURDIR)/proto:/proto:ro" fullstorydev/grpcurl:v1.9.3 \
		-plaintext -import-path /proto -proto flight/v1/flight.proto \
		-H 'x-api-key: $(API_KEY)' -d '$(DATA)' localhost:50051 flight.v1.FlightService/$(METHOD)
