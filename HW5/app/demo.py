import logging
from datetime import UTC, datetime, time, timedelta
from random import Random
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.producer import MovieEvent

log = logging.getLogger("uvicorn.error").getChild("generator")

MOVIES = ("interstellar", "arrival", "dune", "whiplash", "her", "inception", "parasite", "perfect-days")
DEVICES = ("MOBILE", "DESKTOP", "TV", "TABLET")
MOVIE_WEIGHTS = (18, 13, 20, 11, 7, 14, 10, 7)


def session(rng, user_id, session_id, anchor, *, end_at_anchor=False):
    movie = rng.choices(MOVIES, weights=MOVIE_WEIGHTS)[0]
    device = rng.choice(DEVICES)
    length = rng.randint(80, 130) * 60
    events = []
    if rng.random() < 0.65:
        events.append(("SEARCHED", -20, None))
    events.append(("VIEW_STARTED", 0, 0))
    if rng.random() < 0.72:
        progress = rng.randint(300, length // 2)
        pause = rng.randint(15, 180)
        if rng.random() < 0.55:
            events.extend([
                ("VIEW_PAUSED", progress, progress),
                ("VIEW_RESUMED", progress + pause, progress + 1),
            ])
        else:
            pause = 0
        events.append(("VIEW_FINISHED", length + pause, length))
        if rng.random() < 0.42:
            events.append(("LIKED", length + pause + 4, None))
    else:
        progress = rng.randint(45, length // 2)
        events.append(("VIEW_PAUSED", progress, progress))

    if end_at_anchor:
        anchor -= timedelta(seconds=events[-1][1])
    for index, (event_type, elapsed, progress) in enumerate(events):
        yield MovieEvent(
            event_id=uuid5(NAMESPACE_URL, f"cinema/{session_id}/{index}"),
            user_id=user_id,
            movie_id=movie,
            event_type=event_type,
            timestamp=anchor + timedelta(seconds=elapsed),
            device_type=device,
            session_id=session_id,
            progress_seconds=progress,
        )


def history(today, history_days):
    first_day = today - timedelta(days=history_days)
    for offset in range(history_days):
        day = first_day + timedelta(days=offset)
        for age in range(min(offset, 7) + 1):
            cohort = day - timedelta(days=age)
            for number in range(18):
                user = f"demo-{cohort}-{number:02}"
                session_id = f"{user}-{day}"
                rng = Random(session_id)
                if age == 0 or rng.random() < 0.60 / (age ** 0.48):
                    minutes = rng.randint(8 * 60, 21 * 60)
                    anchor = datetime.combine(day, time(), UTC) + timedelta(minutes=minutes)
                    yield from session(rng, user, session_id, anchor)


def publish_session(publisher, stop, events):
    for event in events:
        if stop.is_set():
            return
        publisher.publish(event)


def generate(publisher, stop, history_days, interval):
    today = datetime.now(UTC).date()
    while history_days > 0 and not stop.is_set():
        try:
            publish_session(publisher, stop, history(today, history_days))
            break
        except Exception:
            log.exception("demo history interrupted; replaying with the same event IDs in 5s")
            stop.wait(5)

    rng = Random()
    while interval > 0 and not stop.wait(interval):
        now = datetime.now(UTC)
        user = f"demo-live-{rng.randrange(64):02}"
        try:
            publish_session(publisher, stop, session(rng, user, str(uuid4()), now, end_at_anchor=True))
        except Exception:
            log.exception("demo session interrupted")
