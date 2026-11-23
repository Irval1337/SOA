import json
import logging
import time
from datetime import date, datetime, timedelta

import psycopg
from psycopg.errors import DeadlockDetected, SerializationFailure

from app.storage import clickhouse, postgres, query

log = logging.getLogger("uvicorn.error").getChild("aggregation")

DAILY = """
SELECT
    event_date AS day,
    count() AS event_count,
    uniqExact(user_id) AS dau,
    avgOrNullIf(progress_seconds, event_type = 'VIEW_FINISHED') AS avg_watch_seconds,
    countIf(event_type = 'VIEW_STARTED') AS views_started,
    countIf(event_type = 'VIEW_FINISHED') AS views_finished,
    views_finished / nullIf(views_started, 0) AS conversion
FROM cinema.raw_events FINAL
WHERE event_date BETWEEN {start:Date32} AND {end:Date32}
GROUP BY event_date
"""

MOVIES = """
SELECT day, movie_id, views,
       row_number() OVER (PARTITION BY day ORDER BY views DESC, movie_id) AS rank
FROM (
    SELECT event_date AS day, movie_id, count() AS views
    FROM cinema.raw_events FINAL
    WHERE event_date BETWEEN {start:Date32} AND {end:Date32}
      AND event_type = 'VIEW_STARTED'
    GROUP BY event_date, movie_id
)
ORDER BY day, rank
"""

RETENTION = """
WITH viewers AS (
    SELECT
        min(event_date) AS cohort,
        groupUniqArrayIf(event_date, event_date >= {start:Date32}) AS active_dates
    FROM cinema.raw_events FINAL
    WHERE event_date <= least(addDays({end:Date32}, 7), {today:Date32})
      AND event_type IN ('VIEW_STARTED', 'VIEW_PAUSED', 'VIEW_RESUMED', 'VIEW_FINISHED')
    GROUP BY user_id
    HAVING cohort BETWEEN {start:Date32} AND {end:Date32}
)
SELECT
    cohort AS day,
    age,
    count() AS cohort_size,
    if(addDays(cohort, age) > {today:Date32}, NULL,
       countIf(has(active_dates, addDays(cohort, age))) / cohort_size) AS retention
FROM viewers
ARRAY JOIN range(8) AS age
GROUP BY cohort, age
"""

UPSERT = """
INSERT INTO daily_metrics (day, metric, dimension, value, rank, computed_at)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (day, metric, dimension) DO UPDATE
SET value = EXCLUDED.value, rank = EXCLUDED.rank, computed_at = EXCLUDED.computed_at
"""


def snapshots(start: date, end: date, computed_at: datetime):
    params = {"start": start.isoformat(), "end": end.isoformat()}
    daily = {row["day"]: row for row in query(DAILY, params)}
    cohorts = {}
    for row in query(RETENTION, {**params, "today": computed_at.date().isoformat()}):
        cohorts.setdefault(row["day"], {})[row["age"]] = row
    movies = {}
    for row in query(MOVIES, params):
        movies.setdefault(row["day"], []).append(
            ("movie_views", row["movie_id"], row["views"], row["rank"])
        )

    result = []
    processed = 0
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        stats = daily.get(day, {})
        cohort = cohorts.get(day, {})
        processed += stats.get("event_count", 0)
        metrics = [
            (metric, "", stats.get(metric, default), None)
            for metric, default in (
                ("dau", 0), ("avg_watch_seconds", None),
                ("views_started", 0), ("views_finished", 0), ("conversion", None),
            )
        ]
        metrics.append(("cohort_size", "", cohort.get(0, {}).get("cohort_size", 0), None))
        metrics.extend(
            (f"retention_d{age}", "", cohort.get(age, {}).get("retention"), None)
            for age in range(8)
        )
        metrics.extend(movies.get(day, []))
        result.append((day, metrics))
    return result, processed


def save_clickhouse(snapshots, computed_at: datetime):
    rows = [
        json.dumps({
            "day": day,
            "computed_at": computed_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "metrics": [
                {"metric": name, "dimension": dimension, "value": value, "rank": rank}
                for name, dimension, value, rank in metrics
            ],
        }, allow_nan=False)
        for day, metrics in snapshots
    ]
    clickhouse("INSERT INTO cinema.daily_snapshots FORMAT JSONEachRow\n" + "\n".join(rows))


def save_postgres(snapshots, computed_at: datetime):
    for attempt in range(3):
        try:
            with postgres() as connection, connection.cursor() as cursor:
                for day, metrics in snapshots:
                    previous = cursor.execute(
                        "SELECT metric, dimension, value, rank FROM daily_metrics WHERE day = %s",
                        (day,),
                    ).fetchall()
                    if set(previous) != set(metrics):
                        cursor.execute("DELETE FROM daily_exports WHERE day = %s", (day,))
                    cursor.executemany(UPSERT, [(day, *metric, computed_at) for metric in metrics])
                    cursor.execute(
                        "DELETE FROM daily_metrics WHERE day = %s AND computed_at <> %s",
                        (day, computed_at),
                    )
            return
        except (psycopg.OperationalError, DeadlockDetected, SerializationFailure) as exc:
            log.warning("PostgreSQL write failed attempt=%d: %s", attempt + 1, exc)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
