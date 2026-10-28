import asyncio

import grpc
import pytest

from app import resilience
from app.resilience import (
    CircuitBreaker,
    CircuitBreakerInterceptor,
    CircuitOpenError,
    CircuitState,
    RetryInterceptor,
)

DETAILS = grpc.aio.ClientCallDetails(b"/flight.v1.FlightService/GetFlight", None, None, None, None)


def rpc_error(code: grpc.StatusCode) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details=code.name)


class FakeService:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self, details, request):
        self.calls += 1
        outcome = self.outcomes.pop(0)

        async def call():
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return call()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def sleeps(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(resilience.asyncio, "sleep", fake_sleep)
    return delays


def intercept(interceptor, service):
    return asyncio.run(interceptor.intercept_unary_unary(service, DETAILS, object()))


def test_retry_with_exponential_backoff_then_success(sleeps):
    service = FakeService(rpc_error(grpc.StatusCode.UNAVAILABLE), rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED), "ok")
    assert intercept(RetryInterceptor(3, 0.1), service) == "ok"
    assert service.calls == 3
    assert sleeps == [0.1, 0.2]


def test_retry_gives_up_after_max_attempts(sleeps):
    service = FakeService(*[rpc_error(grpc.StatusCode.UNAVAILABLE)] * 3)
    with pytest.raises(grpc.aio.AioRpcError):
        intercept(RetryInterceptor(3, 0.1), service)
    assert service.calls == 3


@pytest.mark.parametrize(
    "code",
    [grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.RESOURCE_EXHAUSTED],
)
def test_no_retry_for_business_errors(sleeps, code):
    service = FakeService(rpc_error(code))
    with pytest.raises(grpc.aio.AioRpcError) as e:
        intercept(RetryInterceptor(3, 0.1), service)
    assert e.value.code() == code
    assert service.calls == 1
    assert sleeps == []


def make_breaker(clock):
    return CircuitBreaker(failure_threshold=3, window_seconds=10, open_timeout_seconds=15, clock=clock)


def test_breaker_opens_after_threshold_failures_in_window():
    clock = FakeClock()
    breaker = make_breaker(clock)
    breaker.record_failure()
    clock.now = 11
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_breaker_half_open_lets_single_probe_and_closes_on_success():
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now = 15
    breaker.before_call()
    assert breaker.state is CircuitState.HALF_OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    breaker.before_call()


def test_breaker_half_open_reopens_on_failure():
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now = 15
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_breaker_interceptor_counts_only_infrastructure_errors():
    breaker = make_breaker(FakeClock())
    interceptor = CircuitBreakerInterceptor(breaker)
    for _ in range(5):
        with pytest.raises(grpc.aio.AioRpcError):
            intercept(interceptor, FakeService(rpc_error(grpc.StatusCode.NOT_FOUND)))
    assert breaker.state is CircuitState.CLOSED

    for _ in range(3):
        with pytest.raises(grpc.aio.AioRpcError):
            intercept(interceptor, FakeService(rpc_error(grpc.StatusCode.UNAVAILABLE)))
    assert breaker.state is CircuitState.OPEN

    service = FakeService("ok")
    with pytest.raises(CircuitOpenError):
        intercept(interceptor, service)
    assert service.calls == 0
