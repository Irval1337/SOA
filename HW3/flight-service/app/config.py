import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    grpc_port: int
    redis_sentinels: list[tuple[str, int]]
    redis_master_name: str
    cache_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        sentinels = []
        for address in os.environ["REDIS_SENTINELS"].split(","):
            host, port = address.strip().rsplit(":", 1)
            sentinels.append((host, int(port)))
        return cls(
            database_url=os.environ["DATABASE_URL"],
            api_key=os.environ["FLIGHT_SERVICE_API_KEY"],
            grpc_port=int(os.environ.get("GRPC_PORT", "50051")),
            redis_sentinels=sentinels,
            redis_master_name=os.environ.get("REDIS_MASTER_NAME", "mymaster"),
            cache_ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "300")),
        )
