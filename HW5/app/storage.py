import json
import os

import boto3
import httpx
import psycopg
from botocore.config import Config


def clickhouse(sql: str, parameters=None, *, timeout=30):
    response = httpx.post(
        os.environ["CLICKHOUSE_URL"],
        auth=(os.environ["CLICKHOUSE_USER"], os.environ["CLICKHOUSE_PASSWORD"]),
        params={f"param_{name}": value for name, value in (parameters or {}).items()},
        content=sql.encode(),
        timeout=timeout,
    )
    if response.is_error:
        raise RuntimeError(response.text.strip())
    return response.text


def query(sql: str, parameters=None):
    result = clickhouse(sql + "\nSETTINGS output_format_json_quote_64bit_integers = 0 FORMAT JSONEachRow", parameters)
    return [json.loads(line) for line in result.splitlines()]


def postgres():
    return psycopg.connect(os.environ["POSTGRES_DSN"], connect_timeout=3)


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=8,
            retries={"total_max_attempts": 2},
        ),
    )
