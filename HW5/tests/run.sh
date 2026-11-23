#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME="movie-analytics-test-$$"
export MOVIE_PORT=0 AGGREGATION_PORT=0 GRAFANA_PORT=0 S3_PORT=0 CLICKHOUSE_PORT=0
export DEMO_HISTORY_DAYS=0 GENERATOR_INTERVAL_SECONDS=0
export AGGREGATION_INTERVAL_SECONDS=2 AGGREGATION_LOOKBACK_DAYS=1

cleanup() {
    docker compose --profile test down --volumes
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker compose --profile test run --build --rm tests
