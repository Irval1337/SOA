import logging
from datetime import date

from google.protobuf import json_format
from google.protobuf.message import Message
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.asyncio.sentinel import Sentinel
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.config import Settings

log = logging.getLogger(__name__)

SET_IF_VERSION = """
if (redis.call('GET', KEYS[2]) or '0') ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""


def create_redis(settings: Settings) -> Redis:
    sentinel = Sentinel(settings.redis_sentinels, socket_timeout=0.2, socket_connect_timeout=0.2)
    return sentinel.master_for(
        settings.redis_master_name,
        socket_timeout=0.2,
        socket_connect_timeout=0.2,
        retry=Retry(NoBackoff(), 1),
        decode_responses=True,
    )


def flight_key(flight_id: int) -> str:
    return f"flight:{flight_id}"


def search_key(origin: str, destination: str, departure_date: date | None) -> str:
    day = departure_date.isoformat() if departure_date else "any"
    return f"search:{origin}:{destination}:{day}"


def version_key(key: str) -> str:
    return f"version:{key}"


class Cache:
    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl = ttl_seconds
        self.set_if_version = redis.register_script(SET_IF_VERSION)
        self.dirty_keys: set[str] = set()

    async def get(self, key: str, message_type: type[Message]) -> tuple[Message | None, str | None]:
        if self.dirty_keys:
            await self.invalidate(*self.dirty_keys)
            if self.dirty_keys:
                return None, None

        try:
            value, version = await self.redis.mget(key, version_key(key))
        except RedisError as e:
            log.warning("cache unavailable, get %s: %r", key, e)
            return None, None

        if value is None:
            log.info("cache miss %s", key)
            return None, version or "0"
        log.info("cache hit %s", key)
        return json_format.Parse(value, message_type()), None

    async def set(self, key: str, message: Message, version: str | None) -> None:
        if version is None:
            return
        value = json_format.MessageToJson(message, indent=None)
        try:
            stored = await self.set_if_version(keys=[key, version_key(key)], args=[version, value, self.ttl])
        except RedisError as e:
            log.warning("cache unavailable, set %s: %r", key, e)
            return
        if not stored:
            log.info("cache skip stale write %s", key)

    async def invalidate(self, *keys: str) -> None:
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                for key in keys:
                    pipe.incr(version_key(key))
                    pipe.expire(version_key(key), self.ttl)
                    pipe.delete(key)
                await pipe.execute()
        except RedisError as e:
            log.warning("cache unavailable, invalidate %s: %r", " ".join(keys), e)
            self.dirty_keys.update(keys)
            return
        self.dirty_keys.difference_update(keys)
        log.info("cache invalidated %s", " ".join(keys))
