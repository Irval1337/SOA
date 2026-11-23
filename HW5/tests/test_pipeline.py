import json
import time
import unittest
from contextlib import closing
from datetime import UTC, datetime, time as clock_time, timedelta, timezone
from uuid import UUID, uuid4

import httpx
from botocore.exceptions import ClientError
from confluent_kafka.admin import AdminClient, ConfigResource, ResourceType

from app.config import BOOTSTRAP_SERVERS, BUCKET, REGISTRY_URL, SUBJECT, TOPIC
from app.storage import postgres, query, s3_client

MOVIE_URL = "http://movie-service:8000"
AGGREGATION_URL = "http://aggregation:8000"
GRAFANA_URL = "http://grafana:3000"


def wait_for(read, ready, timeout=45):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if ready(value):
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for data. Last result: {value!r}")
        time.sleep(0.5)


def read_export(day):
    key = f"daily/{day}/aggregates.json"
    with closing(s3_client()) as s3:
        try:
            result = s3.get_object(Bucket=BUCKET, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "NoSuchBucket"):
                return None
            raise
        with closing(result["Body"]) as body:
            return json.load(body)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.http = httpx.Client(timeout=45)
        self.addCleanup(self.http.close)
        self.today = datetime.now(UTC).date()
        self.day = self.today - timedelta(days=21)
        self.prefix = f"test-{uuid4().hex[:12]}"
        self.first_movie = f"{self.prefix}-arrival"
        self.second_movie = f"{self.prefix}-dune"

    def event(self, viewer, event_type, progress, *, offset=0, movie=None):
        day = self.day + timedelta(days=offset)
        local_time = timezone(timedelta(hours=3))
        timestamp = datetime.combine(
            day, clock_time(13, 25, 30, 123000), local_time
        ) + timedelta(seconds=progress or 0)
        return {
            "event_id": str(uuid4()),
            "user_id": f"{self.prefix}-{viewer}",
            "movie_id": movie or self.first_movie,
            "event_type": event_type,
            "timestamp": timestamp.isoformat(),
            "device_type": "TV",
            "session_id": f"{self.prefix}-{viewer}-{offset}",
            "progress_seconds": progress,
        }

    def send(self, event):
        response = self.http.post(f"{MOVIE_URL}/events", json=event)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["event_id"]

    def recalculate(self):
        response = self.http.post(f"{AGGREGATION_URL}/recalculate/{self.day}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def saved_metrics(self):
        with postgres() as connection:
            rows = connection.execute(
                "SELECT metric, dimension, value, rank, computed_at FROM daily_metrics WHERE day = %s",
                (self.day,),
            ).fetchall()
        return {(name, dimension): (value, rank, computed_at) for name, dimension, value, rank, computed_at in rows}

    def test_schema_and_replication(self):
        response = self.http.get(f"{REGISTRY_URL}/subjects/{SUBJECT}/versions/1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["version"], 1)
        admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
        metadata = admin.list_topics(TOPIC, timeout=10)
        self.assertGreaterEqual(len(metadata.brokers), 3)
        partitions = metadata.topics[TOPIC].partitions.values()
        self.assertGreaterEqual(len(partitions), 3)
        for partition in partitions:
            self.assertGreaterEqual(len(partition.replicas), 3)
        resource = ConfigResource(ResourceType.TOPIC, TOPIC)
        config = admin.describe_configs([resource], request_timeout=10)[resource].result()
        self.assertEqual(config["min.insync.replicas"].value, "2")

    def test_rejects_invalid_events(self):
        event = self.event("invalid", "VIEW_STARTED", 0)
        for field, value in [("event_type", "WATCHED"), ("timestamp", "2026-01-01T12:30:00"),
                             ("timestamp", "1790000000"), ("event_id", "invalid-uuid"),
                             ("progress_seconds", None), ("progress_seconds", 1.5)]:
            with self.subTest(field=field, value=value):
                response = self.http.post(f"{MOVIE_URL}/events", json={**event, field: value})
                self.assertEqual(response.status_code, 422, response.text)

    def test_events_aggregates_dashboard_and_export(self):
        started = self.event("a", "VIEW_STARTED", 0)
        del started["event_id"]
        started["event_id"] = self.send(started)
        UUID(started["event_id"])

        events = [
            self.event("a", "VIEW_FINISHED", 120),
            self.event("b", "VIEW_STARTED", 0),
            self.event("b", "VIEW_FINISHED", 240),
            self.event("c", "VIEW_STARTED", 0, movie=self.second_movie),
            self.event("d", "SEARCHED", None),
            self.event("c", "VIEW_STARTED", 0, offset=-2),
            self.event("a", "SEARCHED", None, offset=-1),
            self.event("a", "VIEW_STARTED", 0, offset=1),
            self.event("b", "LIKED", None, offset=1),
            self.event("c", "VIEW_STARTED", 0, offset=1),
            self.event("b", "VIEW_STARTED", 0, offset=7, movie=self.second_movie),
        ]
        for event in events + [events[0]]:
            self.assertEqual(self.send(event), event["event_id"])

        rows = wait_for(
            lambda: query(
                """
                SELECT event_id, user_id, movie_id, event_type, timestamp, device_type,
                       session_id, progress_seconds
                FROM cinema.raw_events FINAL
                WHERE startsWith(user_id, {prefix:String})
                """,
                {"prefix": self.prefix},
            ),
            lambda rows: len(rows) == len(events) + 1,
        )
        actual = {row["event_id"]: row for row in rows}
        for event in events + [started]:
            timestamp = datetime.fromisoformat(event["timestamp"]).astimezone(UTC)
            expected = {**event, "timestamp": timestamp.isoformat(sep=" ", timespec="milliseconds")[:-6]}
            self.assertEqual(actual[event["event_id"]], expected)
        self.assertEqual(self.recalculate()["events"], 6)

        expected = {
            ("dau", ""): (4, None),
            ("avg_watch_seconds", ""): (180, None),
            ("views_started", ""): (3, None),
            ("views_finished", ""): (2, None),
            ("conversion", ""): (2 / 3, None),
            ("cohort_size", ""): (2, None),
            ("retention_d0", ""): (1, None),
            ("retention_d1", ""): (0.5, None),
            ("retention_d7", ""): (0.5, None),
            ("movie_views", self.first_movie): (2, 1),
            ("movie_views", self.second_movie): (1, 2),
        }
        expected.update({(f"retention_d{age}", ""): (0, None) for age in range(2, 7)})
        before = self.saved_metrics()
        self.assertEqual({key: row[:2] for key, row in before.items()}, expected)
        self.recalculate()
        after = self.saved_metrics()
        self.assertEqual({key: row[:2] for key, row in after.items()}, expected)
        self.assertGreater(after[("dau", "")][2], before[("dau", "")][2])

        rows = query(
            "SELECT metric, dimension, value, rank FROM cinema.daily_metrics WHERE day = {day:Date32}",
            {"day": self.day.isoformat()},
        )
        self.assertEqual(len(rows), len(expected))
        self.assertEqual(
            {(row["metric"], row["dimension"]): (row["value"], row["rank"]) for row in rows},
            expected,
        )

        for _ in range(2):
            response = self.http.post(f"{AGGREGATION_URL}/export/{self.day}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["uri"], f"s3://{BUCKET}/daily/{self.day}/aggregates.json")
        exported = read_export(self.day)
        self.assertEqual(exported["date"], self.day.isoformat())
        self.assertEqual(datetime.fromisoformat(exported["computed_at"]), after[("dau", "")][2])
        self.assertEqual(
            {(row["metric"], row["dimension"]): (row["value"], row["rank"]) for row in exported["metrics"]},
            expected,
        )
        with closing(s3_client()) as s3:
            listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"daily/{self.day}/")
            self.assertEqual(listing["KeyCount"], 1)

        self.assert_dashboard()

    def assert_dashboard(self):
        response = self.http.get(f"{GRAFANA_URL}/api/dashboards/uid/online-cinema")
        self.assertEqual(response.status_code, 200, response.text)
        panels = response.json()["dashboard"]["panels"]
        queries = [{**panel["targets"][0], "refId": str(panel["id"])} for panel in panels]
        start = datetime.combine(self.day, clock_time(), UTC)
        end = start + timedelta(days=1) - timedelta(seconds=1)
        response = self.http.post(
            f"{GRAFANA_URL}/api/ds/query",
            json={"from": str(int(start.timestamp() * 1000)),
                  "to": str(int(end.timestamp() * 1000)), "queries": queries},
        )
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()["results"]
        self.assertEqual(len(results), 5)
        frame = results["4"]["frames"][0]
        fields = [field["name"] for field in frame["schema"]["fields"]]
        self.assertEqual(fields, ["Cohort", "Users"] + [f"Day {age}" for age in range(8)])
        self.assertEqual(
            frame["data"]["values"],
            [[str(self.day)], [2], [1], [0.5], [0], [0], [0], [0], [0], [0.5]],
        )

    def test_scheduled_export(self):
        yesterday = self.today - timedelta(days=1)
        exported = wait_for(lambda: read_export(yesterday), lambda payload: payload is not None)
        self.assertEqual(exported["date"], yesterday.isoformat())
        self.assertIn("dau", {row["metric"] for row in exported["metrics"]})
