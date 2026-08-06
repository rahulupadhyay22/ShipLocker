"""Circuit breaker for outbound calls to external dependencies.

Trips after N consecutive failures/timeouts, then fast-fails for a cooldown
window instead of letting request threads pile up waiting on a degraded
dependency. Also caps in-flight concurrency per process so one slow
dependency can't claim every gthread worker thread.

State lives in Django's cache (shared across gunicorn workers when Redis is
configured, same pattern as RateLimitMiddleware; degrades to per-process
tracking on LocMemCache).
"""

import logging
import threading
import time
from contextlib import contextmanager

from django.core.cache import cache

logger = logging.getLogger('security')


class CircuitOpenError(Exception):
    """Raised instead of attempting the call while the circuit is open."""


class CircuitBreaker:
    def __init__(self, name, fail_threshold=5, reset_timeout=60, max_concurrency=4):
        self.name = name
        self.fail_threshold = fail_threshold
        self.reset_timeout = reset_timeout
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self._open_key = f'circuit_breaker:{name}:open_until'
        self._fail_key = f'circuit_breaker:{name}:fails'

    def _is_open(self):
        open_until = cache.get(self._open_key)
        return open_until is not None and time.time() < open_until

    def _record_failure(self):
        cache.add(self._fail_key, 0, timeout=self.reset_timeout)
        fails = cache.incr(self._fail_key)
        if fails >= self.fail_threshold:
            # The open marker's own cache entry must outlive reset_timeout --
            # otherwise it expires at the exact moment _is_open() would flip
            # false, and _record_success() can never see "this just recovered"
            # (the trial call after cooldown would find no marker at all).
            cache.set(self._open_key, time.time() + self.reset_timeout, timeout=self.reset_timeout * 10)
            logger.warning(f"Circuit breaker '{self.name}' OPEN for {self.reset_timeout}s after {fails} failures")

    def _record_success(self):
        # Log the recovery side of the OPEN log line above -- otherwise ops
        # only ever sees "it broke" in the logs and never "it's back".
        was_open = cache.get(self._open_key) is not None
        had_failures = cache.get(self._fail_key) is not None

        cache.delete(self._fail_key)
        cache.delete(self._open_key)

        if was_open:
            logger.warning(f"Circuit breaker '{self.name}' RECOVERED — closing circuit")
        elif had_failures:
            logger.info(f"Circuit breaker '{self.name}' failure streak cleared by a successful call")

    @contextmanager
    def call(self):
        """Guard a single external call. Raises CircuitOpenError without
        touching the network if the circuit is open or at capacity."""
        if self._is_open():
            raise CircuitOpenError(f"{self.name} circuit is open")

        if not self._sem.acquire(blocking=False):
            raise CircuitOpenError(f"{self.name} at max concurrency")

        try:
            yield
        except Exception:
            self._record_failure()
            raise
        else:
            self._record_success()
        finally:
            self._sem.release()
