import logging
import sys
import time

import httpx
from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from app.config import BOOTSTRAP_SERVERS, REGISTRY_URL, ROOT, SCHEMA_FILE, SCHEMA_VERSION, SUBJECT, TOPIC
from app.storage import clickhouse, postgres

log = logging.getLogger(__name__)


def schema_metadata(registry: httpx.Client):
    response = registry.post(
        f"/subjects/{SUBJECT}", json={"schema": SCHEMA_FILE.read_text(), "schemaType": "AVRO"}
    )
    response.raise_for_status()
    schema = response.json()
    if schema["version"] != SCHEMA_VERSION:
        raise RuntimeError(f"{SUBJECT}: expected version {SCHEMA_VERSION}, got {schema['version']}")
    return schema


def prepare_kafka():
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    topic = NewTopic(TOPIC, 3, 3, config={"min.insync.replicas": "2"})
    try:
        admin.create_topics([topic], request_timeout=20)[TOPIC].result()
    except KafkaException as exc:
        if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise
    with httpx.Client(base_url=REGISTRY_URL, timeout=15) as registry:
        response = registry.put(f"/config/{SUBJECT}", json={"compatibility": "BACKWARD_TRANSITIVE"})
        response.raise_for_status()
        response = registry.post(
            f"/subjects/{SUBJECT}/versions",
            json={"schema": SCHEMA_FILE.read_text(), "schemaType": "AVRO"},
        )
        response.raise_for_status()
        schema = schema_metadata(registry)
    log.info("topic=%s subject=%s version=%s schema_id=%s", TOPIC, SUBJECT, schema["version"], schema["id"])


def migrate():
    for path in sorted((ROOT / "sql").glob("clickhouse-*.sql")):
        for statement in path.read_text().split(";"):
            if statement.strip():
                clickhouse(statement)
        log.info("applied %s", path.name)
    with postgres() as connection:
        for path in sorted((ROOT / "sql").glob("postgres-*.sql")):
            connection.execute(path.read_text())
            log.info("applied %s", path.name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    action = {"kafka": prepare_kafka, "databases": migrate}[sys.argv[1]]
    for attempt in range(5):
        try:
            action()
            break
        except Exception:
            if attempt == 4:
                raise
            log.exception("bootstrap attempt %d failed", attempt + 1)
            time.sleep(2 ** attempt)
