import io
import json
import logging
import os
import struct
from concurrent.futures import Future, TimeoutError
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Event, Thread
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

import httpx
from confluent_kafka import KafkaException, Producer
from fastapi import FastAPI, HTTPException, Request
from fastavro import parse_schema, schemaless_writer
from fastavro.validation import validate
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.bootstrap import schema_metadata
from app.config import BOOTSTRAP_SERVERS, REGISTRY_URL, SCHEMA_FILE, TOPIC

log = logging.getLogger("uvicorn.error").getChild("producer")
EventType = Literal["VIEW_STARTED", "VIEW_FINISHED", "VIEW_PAUSED", "VIEW_RESUMED", "LIKED", "SEARCHED"]
DeviceType = Literal["MOBILE", "DESKTOP", "TV", "TABLET"]
Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]


class MovieEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    user_id: Identifier
    movie_id: Identifier
    event_type: EventType
    timestamp: datetime
    device_type: DeviceType
    session_id: Identifier
    progress_seconds: Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)] | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def require_iso_timestamp(cls, value):
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or value[10:11] not in ("T", "t", " "):
            raise ValueError("timestamp must be an ISO 8601 datetime with a timezone")
        return value

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        try:
            value = value.astimezone(UTC)
        except OverflowError as exc:
            raise ValueError("UTC timestamp is outside the supported range") from exc
        if not 1900 <= value.year <= 2299:
            raise ValueError("UTC timestamp must be in the range 1900–2299")
        return value.replace(microsecond=value.microsecond // 1000 * 1000)

    @model_validator(mode="after")
    def check_progress(self) -> Self:
        if self.event_type.startswith("VIEW_") and self.progress_seconds is None:
            raise ValueError("progress_seconds is required for view events")
        return self


class Publisher:
    def __init__(self, schema_id: int):
        self.schema = parse_schema(json.loads(SCHEMA_FILE.read_text()))
        self.header = struct.pack(">BI", 0, schema_id)
        self.producer = Producer({
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "enable.idempotence": True,
            "acks": "all",
            "partitioner": "murmur2",
            "retry.backoff.ms": 100,
            "retry.backoff.max.ms": 2000,
            "delivery.timeout.ms": 15000,
        })
        self.stop = Event()
        self.poller = Thread(target=self.poll, name="kafka-delivery")
        self.poller.start()

    def poll(self):
        while not self.stop.is_set():
            self.producer.poll(0.2)

    def close(self):
        self.stop.set()
        self.poller.join()
        self.producer.flush(15)

    def publish(self, event: MovieEvent):
        value = io.BytesIO(self.header)
        value.seek(len(self.header))
        data = event.model_dump()
        data["event_id"] = str(event.event_id)
        validate(data, self.schema, strict=True)
        schemaless_writer(value, self.schema, data, strict=True)

        delivered = Future()

        def on_delivery(error, message):
            if error:
                delivered.set_exception(KafkaException(error))
            else:
                delivered.set_result(None)

        self.producer.produce(
            TOPIC, key=event.user_id.encode(), value=value.getvalue(), on_delivery=on_delivery
        )
        delivered.result(timeout=18)
        log.info(
            "published event_id=%s event_type=%s timestamp=%s",
            event.event_id, event.event_type, event.timestamp.isoformat(),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.demo import generate

    history_days = int(os.environ.get("DEMO_HISTORY_DAYS", "14"))
    interval = float(os.environ.get("GENERATOR_INTERVAL_SECONDS", "4"))
    with httpx.Client(base_url=REGISTRY_URL, timeout=10) as registry:
        schema_id = schema_metadata(registry)["id"]
    app.state.publisher = publisher = Publisher(schema_id)
    stop = Event()
    demo = Thread(target=generate, args=(publisher, stop, history_days, interval), name="demo-generator")
    if history_days > 0 or interval > 0:
        demo.start()
    try:
        yield
    finally:
        stop.set()
        if demo.ident is not None:
            demo.join()
        publisher.close()


app = FastAPI(title="Movie Service", lifespan=lifespan)


@app.get("/health")
def health(request: Request):
    try:
        topic = request.app.state.publisher.producer.list_topics(TOPIC, timeout=2).topics[TOPIC]
        if topic.error is None and all(partition.leader >= 0 for partition in topic.partitions.values()):
            return {"status": "ok"}
    except (KafkaException, KeyError):
        pass
    raise HTTPException(503, "Kafka topic is unavailable")


@app.post("/events", status_code=201)
def create_event(event: MovieEvent, request: Request):
    try:
        request.app.state.publisher.publish(event)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Event does not match the Avro schema") from exc
    except (BufferError, KafkaException, TimeoutError) as exc:
        log.warning("publish failed event_id=%s: %s", event.event_id, exc)
        raise HTTPException(503, "Kafka did not confirm the event") from exc
    return {"event_id": event.event_id}
