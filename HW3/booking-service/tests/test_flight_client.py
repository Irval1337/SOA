import asyncio
from uuid import uuid4

import grpc
import pytest

from app.config import Settings
from app.flight_client import FlightClient, create_channel
from app.resilience import CircuitBreaker, CircuitOpenError, CircuitState

SETTINGS = Settings(
    database_url="",
    flight_service_addr="127.0.0.1:1",
    flight_service_api_key="key",
    flight_rpc_timeout_seconds=1,
    retry_max_attempts=3,
    retry_base_delay_seconds=0.001,
    cb_failure_threshold=1,
    cb_window_seconds=30,
    cb_open_timeout_seconds=30,
)


def test_compensation_is_not_blocked_by_open_breaker():
    async def scenario():
        breaker = CircuitBreaker(1, 30, 30)
        breaker.record_failure()
        assert breaker.state is CircuitState.OPEN

        channel = create_channel(SETTINGS, breaker)
        compensation_channel = create_channel(SETTINGS, breaker=None)
        client = FlightClient(channel, compensation_channel, "key", 1)
        try:
            with pytest.raises(CircuitOpenError):
                await client.release_reservation(uuid4())
            with pytest.raises(grpc.aio.AioRpcError) as e:
                await client.compensate_reservation(uuid4())
            assert e.value.code() == grpc.StatusCode.UNAVAILABLE
        finally:
            await channel.close()
            await compensation_channel.close()

    asyncio.run(scenario())
