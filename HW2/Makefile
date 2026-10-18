COMPOSE := docker compose

.PHONY: generate up up-d down logs test psql expired-token

generate:
	@if command -v uv >/dev/null 2>&1; then \
		cd marketplace && uv run --no-project --python 3.12 --with-requirements requirements.txt ./codegen/generate.sh; \
	else \
		docker run --rm -u $$(id -u):$$(id -g) -e HOME=/tmp -e PIP_DISABLE_PIP_VERSION_CHECK=1 -v "$(CURDIR)/marketplace:/src" -w /src python:3.12-slim-bookworm \
			sh -c 'pip install --user -q --no-warn-script-location -r requirements.txt && PATH=/tmp/.local/bin:$$PATH ./codegen/generate.sh'; \
	fi

up:
	$(COMPOSE) up --build

up-d:
	$(COMPOSE) up --build -d --wait

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f app

test:
	$(COMPOSE) --profile test run --rm --build tests

psql:
	$(COMPOSE) exec postgres psql -U marketplace -d marketplace

expired-token:
	@$(COMPOSE) exec -T app python -m app.tools.expired_token
