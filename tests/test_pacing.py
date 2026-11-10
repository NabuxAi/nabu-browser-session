from __future__ import annotations

import random
from pathlib import Path

import pytest

from nabu_browser_session import Clock, ConfigurationError, RateLimiter, SessionKey, Settings


class FakeClock(Clock):
    def __init__(self) -> None:
        self.t = 10_000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _limiter(path: Path, clock: FakeClock, **kwargs: object) -> RateLimiter:
    defaults = dict(
        path=path,
        capacity=1,
        refill_per_minute=60,
        min_interval_seconds=0,
        jitter_seconds=0,
        enabled=True,
        clock=clock,
        rng=random.Random(0),
    )
    defaults.update(kwargs)
    return RateLimiter(**defaults)  # type: ignore[arg-type]


def test_token_bucket_spaces_same_account(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = _limiter(tmp_path / "pace.db", clock)
    key = SessionKey.parse("owner", "instagram", "me")
    assert limiter.reserve(key).wait_seconds == 0
    assert limiter.reserve(key).wait_seconds == pytest.approx(1)
    assert limiter.reserve(key).wait_seconds == pytest.approx(2)


def test_min_interval_and_jitter(tmp_path: Path) -> None:
    clock = FakeClock()

    class Sequence:
        def uniform(self, low: float, high: float) -> float:
            return 1.5

    limiter = _limiter(
        tmp_path / "pace.db",
        clock,
        capacity=10,
        refill_per_minute=6000,
        min_interval_seconds=5,
        jitter_seconds=4,
        rng=Sequence(),
    )
    key = SessionKey.parse("owner", "whatsapp", "me")
    first = limiter.reserve(key)
    assert first.wait_seconds == pytest.approx(1.5)
    second = limiter.reserve(key)
    # Next slot is min_interval plus the first jitter; this call adds its own jitter.
    assert second.wait_seconds == pytest.approx(8.0)


def test_accounts_do_not_share_a_bucket(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = _limiter(tmp_path / "pace.db", clock, min_interval_seconds=5, jitter_seconds=0, capacity=10)
    one = SessionKey.parse("owner", "tiktok", "one")
    two = SessionKey.parse("owner", "tiktok", "two")
    assert limiter.reserve(one).wait_seconds == 0
    assert limiter.reserve(two).wait_seconds == 0
    assert limiter.reserve(one).wait_seconds == pytest.approx(5)


def test_bucket_survives_a_new_limiter(tmp_path: Path) -> None:
    clock = FakeClock()
    path = tmp_path / "pace.db"
    key = SessionKey.parse("owner", "linkedin", "me")
    first = _limiter(path, clock, min_interval_seconds=8, jitter_seconds=0, capacity=10)
    assert first.reserve(key).wait_seconds == 0
    second = _limiter(path, clock, min_interval_seconds=8, jitter_seconds=0, capacity=10)
    assert second.reserve(key).wait_seconds == pytest.approx(8)


def test_blocking_acquire_uses_the_clock(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = _limiter(tmp_path / "pace.db", clock, min_interval_seconds=3, jitter_seconds=0, capacity=10)
    key = SessionKey.parse("owner", "snapchat", "me")
    limiter.acquire_blocking(key)
    limiter.acquire_blocking(key)
    assert clock.slept == [0, 3]


def test_disabled_limiter_does_not_wait(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = _limiter(tmp_path / "pace.db", clock, enabled=False, capacity=1)
    key = SessionKey.parse("owner", "facebook", "me")
    assert limiter.reserve(key).wait_seconds == 0
    assert limiter.reserve(key).wait_seconds == 0


def test_cost_above_capacity_raises(tmp_path: Path) -> None:
    limiter = _limiter(tmp_path / "pace.db", FakeClock(), capacity=1)
    with pytest.raises(ConfigurationError):
        limiter.reserve("bucket", cost=2)


def test_env_defaults_are_slow(tmp_path: Path) -> None:
    settings = Settings.from_env({"SESSION_DIR": str(tmp_path)})
    assert settings.rate_capacity == 3
    assert settings.rate_refill_per_minute == 3
    assert settings.rate_min_interval_seconds == 12
    assert settings.rate_jitter_seconds == 4
    assert settings.rate_enabled is True
