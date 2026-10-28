import asyncio
import logging
import time
from collections import deque
from enum import Enum

import grpc

log = logging.getLogger(__name__)

RETRYABLE_CODES = frozenset({grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED})
FAILURE_CODES = RETRYABLE_CODES | {grpc.StatusCode.INTERNAL, grpc.StatusCode.UNKNOWN}


class RetryInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    def __init__(self, max_attempts: int, base_delay_seconds: float):
        self._max_attempts = max_attempts
        self._base_delay = base_delay_seconds

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        for attempt in range(1, self._max_attempts + 1):
            try:
                call = await continuation(client_call_details, request)
                return await call
            except grpc.aio.AioRpcError as e:
                if e.code() not in RETRYABLE_CODES or attempt == self._max_attempts:
                    raise
                delay = self._base_delay * 2 ** (attempt - 1)
                log.warning(
                    "%s failed with %s (attempt %d/%d), retrying in %d ms",
                    client_call_details.method.decode(),
                    e.code().name,
                    attempt,
                    self._max_attempts,
                    delay * 1000,
                )
                await asyncio.sleep(delay)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    def __init__(self, retry_after_seconds: float):
        super().__init__("flight service circuit breaker is open")
        self.retry_after_seconds = retry_after_seconds


class CircuitBreaker:
    def __init__(
        self, failure_threshold: int, window_seconds: float, open_timeout_seconds: float, clock=time.monotonic
    ):
        self._threshold = failure_threshold
        self._window = window_seconds
        self._open_timeout = open_timeout_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        return self._state

    def before_call(self) -> None:
        if self._state is CircuitState.OPEN:
            remaining = self._opened_at + self._open_timeout - self._clock()
            if remaining > 0:
                raise CircuitOpenError(remaining)
            self._transition(CircuitState.HALF_OPEN)
        if self._state is CircuitState.HALF_OPEN:
            if self._probe_in_flight:
                raise CircuitOpenError(1)
            self._probe_in_flight = True

    def record_success(self) -> None:
        if self._state is CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)

    def record_failure(self) -> None:
        if self._state is CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN)
            return
        if self._state is CircuitState.OPEN:
            return
        now = self._clock()
        self._failures.append(now)
        while self._failures and self._failures[0] <= now - self._window:
            self._failures.popleft()
        if len(self._failures) >= self._threshold:
            self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        log.warning("circuit breaker %s -> %s", self._state.value, new_state.value)
        self._state = new_state
        self._failures.clear()
        self._probe_in_flight = False
        if new_state is CircuitState.OPEN:
            self._opened_at = self._clock()


class CircuitBreakerInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    def __init__(self, breaker: CircuitBreaker):
        self._breaker = breaker

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        self._breaker.before_call()
        try:
            call = await continuation(client_call_details, request)
            response = await call
        except grpc.aio.AioRpcError as e:
            if e.code() in FAILURE_CODES:
                self._breaker.record_failure()
            else:
                self._breaker.record_success()
            raise
        except BaseException:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return response
