import json
from contextlib import closing
from datetime import date

from botocore.exceptions import ClientError
from psycopg.rows import dict_row

from app.config import BUCKET
from app.storage import postgres, s3_client


def pending_days(today: date):
    with postgres() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT m.day
            FROM daily_metrics m
            WHERE m.day < %s
              AND NOT EXISTS (SELECT 1 FROM daily_exports e WHERE e.day = m.day)
            ORDER BY m.day
            """,
            (today,),
        ).fetchall()
    return [row[0] for row in rows]


def export_day(day: date):
    with postgres() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT metric, dimension, value, rank, computed_at
            FROM daily_metrics
            WHERE day = %s
            ORDER BY metric, rank NULLS LAST, dimension
            """,
            (day,),
        )
        metrics = cursor.fetchall()
    if not metrics:
        return None

    computed_at = metrics[0]["computed_at"]
    for row in metrics:
        row["computed_at"] = row["computed_at"].isoformat()
    body = json.dumps(
        {"date": day.isoformat(), "computed_at": computed_at.isoformat(), "metrics": metrics},
        ensure_ascii=False, allow_nan=False,
    )
    key = f"daily/{day.isoformat()}/aggregates.json"
    with closing(s3_client()) as s3:
        try:
            s3.head_bucket(Bucket=BUCKET)
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise
            s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode(), ContentType="application/json")

    with postgres() as connection:
        connection.execute(
            """
            INSERT INTO daily_exports (day, computed_at) VALUES (%s, %s)
            ON CONFLICT (day) DO UPDATE
            SET computed_at = EXCLUDED.computed_at, exported_at = now()
            """,
            (day, computed_at),
        )
    return f"s3://{BUCKET}/{key}"
