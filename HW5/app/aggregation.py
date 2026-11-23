import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from threading import Event, Lock, Thread

from fastapi import FastAPI, HTTPException

from app.exporter import export_day, pending_days
from app.metrics import save_clickhouse, save_postgres, snapshots
from app.storage import clickhouse, postgres

log = logging.getLogger("uvicorn.error").getChild("aggregation")
writes = Lock()


def recalculate(start: date, end: date):
    with writes:
        started = time.monotonic()
        computed_at = datetime.now(UTC)
        log.info("aggregation started start=%s end=%s", start, end)
        days, processed = snapshots(start, end, computed_at)
        save_clickhouse(days, computed_at)
        save_postgres(days, computed_at)
        elapsed = round(time.monotonic() - started, 3)
        log.info("aggregation finished start=%s end=%s events=%d elapsed_seconds=%s",
                 start, end, processed, elapsed)
        return {"events": processed, "elapsed_seconds": elapsed, "computed_at": computed_at}


def export(day: date):
    with writes:
        uri = export_day(day)
    if uri:
        log.info("exported day=%s uri=%s", day, uri)
    return uri


def schedule(stop: Event, interval: float, lookback: int):
    while not stop.is_set():
        today = datetime.now(UTC).date()
        try:
            recalculate(today - timedelta(days=lookback), today)
        except Exception:
            log.exception("scheduled aggregation failed")
        try:
            for day in pending_days(today):
                if stop.is_set():
                    return
                try:
                    export(day)
                except Exception:
                    log.exception("scheduled export failed day=%s; retry next cycle", day)
        except Exception:
            log.exception("could not read pending exports; retry next cycle")
        stop.wait(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    interval = float(os.environ.get("AGGREGATION_INTERVAL_SECONDS", "60"))
    lookback = int(os.environ.get("AGGREGATION_LOOKBACK_DAYS", "14"))
    if interval <= 0 or lookback < 1:
        raise ValueError("aggregation interval and lookback must be positive")
    stop = Event()
    scheduler = Thread(target=schedule, args=(stop, interval, lookback), name="aggregation-scheduler")
    scheduler.start()
    try:
        yield
    finally:
        stop.set()
        scheduler.join()


app = FastAPI(title="Aggregation Service", lifespan=lifespan)


@app.get("/health")
def health():
    try:
        clickhouse("SELECT 1", timeout=2)
        with postgres() as connection:
            connection.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(503, "ClickHouse or PostgreSQL is unavailable") from exc
    return {"status": "ok"}


@app.post("/recalculate/{day}")
def recalculate_date(day: date):
    if not date(1900, 1, 1) <= day <= datetime.now(UTC).date():
        raise HTTPException(422, "date must be between 1900-01-01 and today (UTC)")
    try:
        result = recalculate(day, day)
    except Exception as exc:
        log.exception("manual aggregation failed day=%s", day)
        raise HTTPException(503, "Aggregation failed") from exc
    return {"date": day, **result}


@app.post("/export/{day}")
def export_date(day: date):
    try:
        uri = export(day)
    except Exception as exc:
        log.exception("manual export failed day=%s", day)
        raise HTTPException(503, "Export failed") from exc
    if uri is None:
        raise HTTPException(404, "No aggregates for this date")
    return {"date": day, "uri": uri}
