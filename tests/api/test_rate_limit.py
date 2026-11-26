"""The wrong-key limiter on its own: counts, the window, and the bound on addresses."""

from __future__ import annotations

import pytest

from marquee.api.rate_limit import MAX_CLIENTS, MAX_FAILURES, WINDOW_SECONDS, WrongKeyLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_the_defaults_are_ten_a_minute_for_ten_thousand_addresses() -> None:
    assert (MAX_FAILURES, WINDOW_SECONDS, MAX_CLIENTS) == (10, 60.0, 10_000)


def test_ten_failures_are_allowed_and_then_the_address_waits(clock: Clock) -> None:
    limiter = WrongKeyLimiter(clock=clock)
    reached = []
    for _ in range(10):
        assert limiter.retry_after("10.0.0.1") == 0
        reached.append(limiter.fail("10.0.0.1"))
        clock.now += 1
    assert reached == [False] * 9 + [True]
    # The first failure was at 1000, so the address waits until 1060.
    assert limiter.retry_after("10.0.0.1") == pytest.approx(50.0)
    assert limiter.retry_after("10.0.0.2") == 0


def test_the_wait_ends_when_the_oldest_failure_is_a_minute_old(clock: Clock) -> None:
    limiter = WrongKeyLimiter(clock=clock)
    for _ in range(10):
        limiter.fail("a")
    clock.now += 59.9
    assert limiter.retry_after("a") > 0
    clock.now += 0.1
    assert limiter.retry_after("a") == 0
    assert len(limiter) == 0


def test_failures_spread_over_more_than_a_minute_never_block(clock: Clock) -> None:
    limiter = WrongKeyLimiter(clock=clock)
    for _ in range(100):
        assert limiter.retry_after("a") == 0
        limiter.fail("a")
        clock.now += 6.1


def test_at_most_max_clients_addresses_are_held(clock: Clock) -> None:
    limiter = WrongKeyLimiter(max_clients=3, clock=clock)
    for address in ("a", "b", "c", "d"):
        limiter.fail(address)
    assert len(limiter) == 3
    # The oldest address was forgotten, the newest kept.
    for _ in range(9):
        limiter.fail("d")
    assert limiter.retry_after("d") > 0
    assert limiter.retry_after("a") == 0
