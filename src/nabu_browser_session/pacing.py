"""Per-account token bucket with a minimum gap and jitter.

All unofficial actions for one account share a bucket. The bucket is stored in
the same SQLite file as the vault, so two gateway processes on one volume do
not burst after a restart. Defaults are deliberately slow.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from nabu_browser_session.config import Settings
from nabu_browser_session.db import open_database
from nabu_browser_session.errors import ConfigurationError
from nabu_browser_session.vault import SessionKey


class Clock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


@dataclass(frozen=True)
class PaceTicket:
    """How long an action was asked to wait before it runs."""

    bucket: str
    wait_seconds: float
    unofficial: bool = True

    def public_dict(self) -> dict[str, object]:
        return {
            "unofficial": True,
            "wait_seconds": round(self.wait_seconds, 3),
            "paced": True,
        }


class RateLimiter:
    def __init__(
        self,
        *,
        path: Path,
        capacity: float = 3.0,
        refill_per_minute: float = 3.0,
        min_interval_seconds: float = 12.0,
        jitter_seconds: float = 4.0,
        enabled: bool = True,
        clock: Clock | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if capacity < 1:
            raise ConfigurationError("rate capacity must be at least 1")
        if refill_per_minute <= 0:
            raise ConfigurationError("rate refill must be greater than 0")
        if min_interval_seconds < 0 or jitter_seconds < 0:
            raise ConfigurationError("rate interval and jitter must be >= 0")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_minute) / 60.0
        self.min_interval_seconds = float(min_interval_seconds)
        self.jitter_seconds = float(jitter_seconds)
        self.enabled = enabled
        self._clock = clock or Clock()
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._conn = open_database(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pace_buckets (
                bucket_key TEXT PRIMARY KEY,
                tokens REAL NOT NULL,
                updated_at REAL NOT NULL,
                next_slot REAL NOT NULL
            )
            """
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock | None = None,
        rng: random.Random | None = None,
    ) -> RateLimiter:
        return cls(
            path=settings.session_db_path,
            capacity=settings.rate_capacity,
            refill_per_minute=settings.rate_refill_per_minute,
            min_interval_seconds=settings.rate_min_interval_seconds,
            jitter_seconds=settings.rate_jitter_seconds,
            enabled=settings.rate_enabled,
            clock=clock,
            rng=rng,
        )

    def reserve(self, key: SessionKey | str, *, cost: float = 1.0) -> PaceTicket:
        """Persist the next slot and return the wait. Does not sleep."""
        bucket = key.bucket if isinstance(key, SessionKey) else str(key)
        wait = 0.0 if not self.enabled else self._reserve(bucket, cost)
        return PaceTicket(bucket=bucket, wait_seconds=wait)

    def acquire_blocking(self, key: SessionKey | str, *, cost: float = 1.0) -> PaceTicket:
        ticket = self.reserve(key, cost=cost)
        self._clock.sleep(ticket.wait_seconds)
        return ticket

    async def acquire(self, key: SessionKey | str, *, cost: float = 1.0) -> PaceTicket:
        """Async acquire. The wait yields the event loop so a gateway stays responsive."""
        ticket = self.reserve(key, cost=cost)
        if ticket.wait_seconds > 0:
            await asyncio.sleep(ticket.wait_seconds)
        return ticket

    def _reserve(self, bucket: str, cost: float) -> float:
        if cost <= 0:
            raise ConfigurationError("pace cost must be positive")
        if cost > self.capacity:
            raise ConfigurationError("pace cost exceeds NABU_RATE_CAPACITY")
        now = self._clock.now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT tokens, updated_at, next_slot FROM pace_buckets WHERE bucket_key = ?",
                    (bucket,),
                ).fetchone()
                if row is None:
                    tokens = self.capacity
                    updated_at = now
                    next_slot = now
                else:
                    tokens = float(row["tokens"])
                    updated_at = float(row["updated_at"])
                    next_slot = float(row["next_slot"])
                basis = max(now, updated_at)
                elapsed = basis - updated_at
                tokens = min(self.capacity, tokens + elapsed * self.refill_per_second)
                start = max(now, next_slot)
                if start > basis:
                    tokens = min(self.capacity, tokens + (start - basis) * self.refill_per_second)
                    basis = start
                if tokens < cost:
                    start = basis + ((cost - tokens) / self.refill_per_second)
                    tokens = 0.0
                else:
                    tokens -= cost
                jitter = 0.0
                if self.jitter_seconds:
                    jitter = float(self._rng.uniform(0.0, self.jitter_seconds))
                wait = (start - now) + jitter
                stored_next = start + self.min_interval_seconds + jitter
                self._conn.execute(
                    """
                    INSERT INTO pace_buckets (bucket_key, tokens, updated_at, next_slot)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(bucket_key) DO UPDATE SET
                        tokens = excluded.tokens,
                        updated_at = excluded.updated_at,
                        next_slot = excluded.next_slot
                    """,
                    (bucket, tokens, start, stored_next),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return wait

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def rate_limiter_from_env(
    environ: dict[str, str] | None = None,
    *,
    clock: Clock | None = None,
    rng: random.Random | None = None,
) -> RateLimiter:
    return RateLimiter.from_settings(Settings.from_env(environ), clock=clock, rng=rng)
