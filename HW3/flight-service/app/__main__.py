import asyncio
import logging
import signal

import asyncpg
import grpc

from app.auth import ApiKeyInterceptor
from app.cache import Cache, create_redis
from app.config import Settings
from app.servicer import FlightServicer
from flight.v1 import flight_pb2_grpc as pb_grpc

log = logging.getLogger("flight_service")


async def serve() -> None:
    settings = Settings.from_env()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    cache = Cache(create_redis(settings), settings.cache_ttl_seconds)

    server = grpc.aio.server(interceptors=[ApiKeyInterceptor(settings.api_key)])
    pb_grpc.add_FlightServiceServicer_to_server(FlightServicer(pool, cache), server)
    server.add_insecure_port(f"[::]:{settings.grpc_port}")
    await server.start()
    log.info("flight service listening on :%d", settings.grpc_port)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(server.stop(grace=5)))
    await server.wait_for_termination()
    await pool.close()
    log.info("flight service stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(serve())
