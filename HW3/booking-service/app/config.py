import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    flight_service_addr: str
    flight_service_api_key: str
    flight_rpc_timeout_seconds: float
    retry_max_attempts: int
    retry_base_delay_seconds: float
    cb_failure_threshold: int
    cb_window_seconds: float
    cb_open_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_url=env["DATABASE_URL"],
            flight_service_addr=env["FLIGHT_SERVICE_ADDR"],
            flight_service_api_key=env["FLIGHT_SERVICE_API_KEY"],
            flight_rpc_timeout_seconds=float(env.get("FLIGHT_RPC_TIMEOUT_SECONDS", "2")),
            retry_max_attempts=int(env.get("RETRY_MAX_ATTEMPTS", "3")),
            retry_base_delay_seconds=int(env.get("RETRY_BASE_DELAY_MS", "100")) / 1000,
            cb_failure_threshold=int(env.get("CB_FAILURE_THRESHOLD", "5")),
            cb_window_seconds=float(env.get("CB_WINDOW_SECONDS", "30")),
            cb_open_timeout_seconds=float(env.get("CB_OPEN_TIMEOUT_SECONDS", "15")),
        )
