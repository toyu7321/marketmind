"""Low-overhead request timing for latency work and production diagnostics.

This intentionally records durations and cache outcomes only.  It never stores
request bodies, credentials, authentication tokens, or provider URLs.
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


logger = logging.getLogger(__name__)


@dataclass
class RequestProfile:
    started_at: float = field(default_factory=time.perf_counter)
    phases_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    cache_events: Counter[str] = field(default_factory=Counter)

    def add(self, name: str, elapsed_ms: float) -> None:
        self.phases_ms[name] += round(max(0.0, elapsed_ms), 2)

    def cache(self, group: str, outcome: str) -> None:
        self.cache_events[f"{group}:{outcome}"] += 1


_request_profile: ContextVar[RequestProfile | None] = ContextVar("marketmind_request_profile", default=None)
_endpoint_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
_provider_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=240))


def safe_endpoint_name(path: str) -> str:
    """Avoid retaining resource IDs or symbols in diagnostics and public health."""
    parts = [part for part in path.split("/") if part]
    if len(parts) <= 2:
        return path
    if parts[:2] == ["api", "stocks"]:
        return "/api/stocks/:symbol"
    if parts[:2] == ["api", "options"]:
        return "/api/options/:symbol"
    if len(parts) >= 3 and parts[:1] == ["api"]:
        return f"/{parts[0]}/{parts[1]}/:resource"
    return path


def begin_request() -> object:
    """Create a request-scoped profiler; the middleware must reset its token."""
    return _request_profile.set(RequestProfile())


def current_profile() -> RequestProfile | None:
    return _request_profile.get()


def reset_request(token: object) -> None:
    _request_profile.reset(token)  # type: ignore[arg-type]


@contextmanager
def measure(name: str) -> Iterator[None]:
    """Record a synchronous or awaited code section when a request is active."""
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - started) * 1_000
        profile = current_profile()
        if profile is not None:
            profile.add(name, elapsed)
        if name.startswith("alpaca."):
            _provider_samples[name].append(elapsed)


def cache_event(group: str, outcome: str) -> None:
    profile = current_profile()
    if profile is not None:
        profile.cache(group, outcome)


def finish_request(path: str) -> tuple[RequestProfile, float]:
    profile = current_profile() or RequestProfile()
    total_ms = (time.perf_counter() - profile.started_at) * 1_000
    profile.add("total", total_ms)
    _endpoint_samples[path].append(total_ms)
    return profile, total_ms


def _percentile(values: deque[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 1)


def latency_report() -> dict[str, object]:
    """A small rolling, key-free report safe to expose from the health endpoint."""
    return {
        "endpoint_samples": {
            path: {"samples": len(values), "p50_ms": _percentile(values, 0.5), "p95_ms": _percentile(values, 0.95)}
            for path, values in sorted(_endpoint_samples.items())
        },
        "alpaca_upstream": {
            name.removeprefix("alpaca."): {"samples": len(values), "p50_ms": _percentile(values, 0.5), "p95_ms": _percentile(values, 0.95)}
            for name, values in sorted(_provider_samples.items())
        },
    }


def response_headers(profile: RequestProfile, total_ms: float) -> dict[str, str]:
    """Return standards-based timing and a compact cache outcome summary."""
    metrics = [(name, value) for name, value in profile.phases_ms.items() if name != "total"]
    metrics.append(("total", total_ms))
    server_timing = ", ".join(f"{name.replace('.', '_')};dur={value:.1f}" for name, value in metrics[:12])
    headers = {"Server-Timing": server_timing}
    if profile.cache_events:
        headers["X-MarketMind-Cache"] = ",".join(
            f"{event.replace(':', '-')}-{count}" for event, count in sorted(profile.cache_events.items())
        )
    return headers


def log_request(path: str, profile: RequestProfile, total_ms: float) -> None:
    """Emit structured, secret-free diagnostics for p50/cold-vs-warm investigation."""
    logger.info(
        "request_timing",
        extra={
            "path": path,
            "total_ms": round(total_ms, 1),
            "phases_ms": {key: round(value, 1) for key, value in profile.phases_ms.items()},
            "cache": dict(profile.cache_events),
        },
    )
