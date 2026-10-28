import asyncio
from uuid import uuid4

from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache import Cache, create_redis
from app.config import Settings
from flight.v1 import flight_pb2 as pb

TTL = 60


def run(scenario):
    async def main():
        redis = create_redis(Settings.from_env())
        try:
            await scenario(redis, Cache(redis, TTL), f"test:{uuid4()}")
        finally:
            await redis.aclose()

    asyncio.run(main())


def test_miss_then_hit_with_ttl():
    async def scenario(redis, cache, key):
        value, version = await cache.get(key, pb.Flight)
        assert value is None
        await cache.set(key, pb.Flight(id=1, available_seats=10), version)

        value, _ = await cache.get(key, pb.Flight)
        assert value.available_seats == 10
        assert 0 < await redis.ttl(key) <= TTL

    run(scenario)


def test_invalidate_removes_key_and_version_key_has_ttl():
    async def scenario(redis, cache, key):
        _, version = await cache.get(key, pb.Flight)
        await cache.set(key, pb.Flight(id=1, available_seats=10), version)

        await cache.invalidate(key)
        assert await redis.get(key) is None
        assert 0 < await redis.ttl(f"version:{key}") <= TTL

    run(scenario)


def test_stale_write_after_invalidation_is_skipped():
    async def scenario(redis, cache, key):
        _, version = await cache.get(key, pb.Flight)
        stale = pb.Flight(id=1, available_seats=10)
        await cache.invalidate(key)
        await cache.set(key, stale, version)

        value, _ = await cache.get(key, pb.Flight)
        assert value is None

    run(scenario)


def test_failed_invalidation_bypasses_cache_until_retried():
    async def scenario(redis, cache, key):
        _, version = await cache.get(key, pb.Flight)
        await cache.set(key, pb.Flight(id=1, available_seats=10), version)

        def broken_pipeline(*args, **kwargs):
            raise RedisConnectionError("simulated outage")

        redis.pipeline = broken_pipeline
        await cache.invalidate(key)
        assert await redis.get(key) is not None
        assert await cache.get(key, pb.Flight) == (None, None)

        del redis.pipeline
        value, _ = await cache.get(key, pb.Flight)
        assert value is None
        assert await redis.get(key) is None

    run(scenario)
