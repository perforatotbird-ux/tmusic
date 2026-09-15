"""
T-Music Rate Limiting & Brute-Force Defense
Sliding-window in-memory rate limiter designed for Internet-facing endpoints.

NOTE: state lives in this process only. With multiple uvicorn workers each one
enforces its own budget (an attacker gets N× the allowance), and restarts wipe
counters. For a multi-worker production deployment back this with Redis and key
lockout/attempt counters there instead of here.
"""
import time
import threading
from collections import defaultdict
from typing import Dict, List, Tuple


class SlidingWindowRateLimiter:
    """Thread-safe sliding window rate limiter with lockout support."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._failed_attempts: Dict[str, int] = defaultdict(int)
        self._lockouts: Dict[str, float] = {}

    def is_locked_out(self, client_ip: str) -> Tuple[bool, int]:
        """Check if an IP address is currently locked out."""
        with self._lock:
            now = time.time()
            lockout_until = self._lockouts.get(client_ip, 0.0)
            if lockout_until > now:
                remaining_seconds = int(lockout_until - now)
                return True, remaining_seconds
            elif client_ip in self._lockouts:
                del self._lockouts[client_ip]
                self._failed_attempts[client_ip] = 0
            return False, 0

    def record_failure(self, client_ip: str, max_attempts: int = 5, lockout_seconds: int = 900) -> Tuple[bool, int]:
        """Record a failed login attempt. If limit reached, locks out client."""
        with self._lock:
            self._failed_attempts[client_ip] += 1
            attempts = self._failed_attempts[client_ip]
            if attempts >= max_attempts:
                now = time.time()
                self._lockouts[client_ip] = now + lockout_seconds
                return True, lockout_seconds
            return False, max_attempts - attempts

    def record_success(self, client_ip: str):
        """Reset failed attempt counters on successful authentication."""
        with self._lock:
            self._failed_attempts.pop(client_ip, None)
            self._lockouts.pop(client_ip, None)

    def is_rate_limited(self, key: str, max_requests: int = 60, window_seconds: int = 60) -> bool:
        """Check sliding window request limit."""
        with self._lock:
            now = time.time()
            cutoff = now - window_seconds
            timestamps = self._requests[key]

            # Prune expired timestamps; drop the key entirely when empty so the
            # table cannot grow unbounded over the process lifetime.
            valid_timestamps = [ts for ts in timestamps if ts > cutoff]
            if len(valid_timestamps) >= max_requests:
                self._requests[key] = valid_timestamps
                return True

            valid_timestamps.append(now)
            self._requests[key] = valid_timestamps
            self._maybe_compact(now, window_seconds)
            return False

    def _maybe_compact(self, now: float, window_seconds: int):
        """Occasionally sweep dead keys. Runs at most once per window per process."""
        last = getattr(self, "_last_compact", 0.0)
        if now - last < window_seconds:
            return
        self._last_compact = now
        cutoff = now - window_seconds
        dead = [k for k, v in self._requests.items() if not any(ts > cutoff for ts in v)]
        for k in dead:
            del self._requests[k]
        # Hard safety cap: if a single window still holds absurd key counts
        # (e.g. attacker cycling IPs behind a trusted proxy), drop the oldest.
        if len(self._requests) > 200_000:
            for k in list(self._requests)[: len(self._requests) - 200_000]:
                del self._requests[k]


# Global rate limiter instance
limiter = SlidingWindowRateLimiter()
