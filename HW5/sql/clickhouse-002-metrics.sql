CREATE TABLE IF NOT EXISTS cinema.daily_snapshots
(
    day Date32,
    computed_at DateTime64(6, 'UTC'),
    metrics Array(Tuple(metric String, dimension String, value Nullable(Float64), rank Nullable(UInt32)))
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY day;

CREATE VIEW IF NOT EXISTS cinema.daily_metrics
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT day, computed_at, entry.metric AS metric, entry.dimension AS dimension,
       entry.value AS value, entry.rank AS rank
FROM (SELECT * FROM cinema.daily_snapshots FINAL)
ARRAY JOIN metrics AS entry;

CREATE USER IF NOT EXISTS grafana IDENTIFIED WITH sha256_password BY 'cinema-grafana'
SETTINGS readonly = 1, max_execution_time CHANGEABLE_IN_READONLY;

GRANT SELECT ON cinema.daily_metrics TO grafana;
