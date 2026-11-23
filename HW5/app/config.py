import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPIC = "movie-events"
SUBJECT = f"{TOPIC}-value"
SCHEMA_VERSION = 1
SCHEMA_FILE = ROOT / "schema" / f"movie-event-v{SCHEMA_VERSION}.avsc"
BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka-1:9092,kafka-2:9092,kafka-3:9092")
REGISTRY_URL = os.environ.get("SCHEMA_REGISTRY_URL", "http://schema-registry:8081")
BUCKET = "movie-analytics"
