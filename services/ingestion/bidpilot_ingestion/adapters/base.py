"""The connector SDK: the contract every source adapter implements (SPEC §6.2).

Adding a source must never mean touching pipeline code. A source is a row in the
`sources` table (SPEC §6.1); an adapter is a registered implementation of this protocol,
instantiated per portal via config. That is what makes "one atexo adapter covers 30+
portals" (SPEC §6.4) an architectural fact rather than an aspiration.

Mandatory behaviours, enforced here so no adapter can forget them (SPEC §6.2, §16):
  * declared bot User-Agent,
  * exponential backoff with jitter on transient failures,
  * per-source rate limiting (default <= 1 req/s),
  * a legal basis recorded in the registry before the adapter is allowed to run.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from ..canonical import CanonicalNotice, DocumentRef, RawNotice

USER_AGENT = "BidPilotBot/1.0 (+https://bidpilot.example/bot)"

# Retried: transient upstream conditions. Anything else is a bug or a shape change,
# and retrying it just delays the alert.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 408})


@dataclass(frozen=True)
class Cursor:
    """Incremental-fetch position. Persisted per source between runs.

    `since` is the watermark. `token` carries source-specific continuation state, e.g.
    TED's `iterationNextToken` or an Opendatasoft offset.
    """

    since: datetime | None = None
    token: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LegalBasis:
    """SPEC §24.7: an adapter refuses to run without a recorded legal basis."""

    basis: str  # "open-license" | "tos-reviewed" | "robots-ok"
    notes: str = ""
    reviewed_at: str | None = None

    VALID = ("open-license", "tos-reviewed", "robots-ok")

    def validate(self) -> None:
        if self.basis not in self.VALID:
            raise ValueError(f"legal basis must be one of {self.VALID}, got {self.basis!r}")


@dataclass
class SourceConfig:
    """A row of the `sources` table, as the adapter sees it (SPEC §6.1)."""

    code: str
    country: str
    tier: int
    kind: str  # api | ocds | rss | scrape | email | manual
    base_url: str | None = None
    legal: LegalBasis | None = None
    max_rps: float = 1.0
    timeout_s: float = 60.0
    max_retries: int = 4
    options: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    """SPEC §6.2. Kept as a Protocol so adapters need not inherit to conform."""

    name: str
    version: str

    def fetch_since(self, cursor: Cursor) -> Iterator[RawNotice]:
        """Incremental fetch. MUST be idempotent, MUST respect per-source rate limits,
        MUST persist the raw payload untouched."""
        ...

    def normalize(self, raw: RawNotice) -> CanonicalNotice:
        """Map to the canonical schema. MUST set provenance. MUST NOT invent values."""
        ...

    def fetch_documents(self, notice: CanonicalNotice) -> list[DocumentRef]:
        """DCE document URLs/files when publicly reachable, else deep-link only."""
        ...


def content_hash(payload: Any) -> str:
    """Stable hash of a raw payload. Drives amendment detection (SPEC §6.7).

    Key order must not affect the result, or every re-fetch would look like an amendment.
    """
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class RateLimiter:
    """Minimal per-source throttle. Politeness is a legal posture, not an optimisation."""

    def __init__(self, max_rps: float) -> None:
        self._min_interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self._last = 0.0

    def wait(self, sleep=time.sleep, now=time.monotonic) -> None:
        if self._min_interval <= 0:
            return
        elapsed = now() - self._last
        remaining = self._min_interval - elapsed
        if remaining > 0:
            sleep(remaining)
        self._last = now()


class BaseAdapter(ABC):
    """Shared HTTP plumbing: identification, throttling, retry with jittered backoff."""

    name: str = "base"
    version: str = "0.0.0"

    def __init__(self, config: SourceConfig, client: httpx.Client | None = None) -> None:
        if config.legal is None:
            raise ValueError(
                f"source {config.code!r} has no legal basis recorded; "
                "adapters refuse to run without one (SPEC §24.7)"
            )
        config.legal.validate()
        self.config = config
        self._limiter = RateLimiter(config.max_rps)
        self._client = client or httpx.Client(
            timeout=config.timeout_s,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    @property
    def adapter_ref(self) -> str:
        """`name@version`, written into every notice's provenance (SPEC Annex B.1)."""
        return f"{self.name}@{self.version}"

    def request(self, method: str, url: str, *, sleep=time.sleep, **kwargs: Any) -> httpx.Response:
        """Rate-limited, retrying HTTP call. Raises the last error once retries run out."""
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self._limiter.wait(sleep=sleep)
            try:
                response = self._client.request(method, url, **kwargs)
                if response.status_code in RETRY_STATUS:
                    last_error = httpx.HTTPStatusError(
                        f"{response.status_code} from {url}", request=response.request, response=response
                    )
                else:
                    response.raise_for_status()
                    return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
            if attempt < self.config.max_retries:
                sleep(self._backoff(attempt))
        assert last_error is not None
        raise last_error

    def _backoff(self, attempt: int) -> float:
        """Exponential with full jitter, capped - the standard well-behaved-client curve."""
        return min(2**attempt, 30) * (0.5 + random.random() / 2)

    def raw(self, external_id: str, payload: dict[str, Any], url: str | None = None) -> RawNotice:
        return RawNotice(
            source=self.config.code,
            external_id=external_id,
            payload=payload,
            url=url,
            fetched_at=datetime.now(UTC),
            content_hash=content_hash(payload),
        )

    def fetch_documents(self, notice: CanonicalNotice) -> list[DocumentRef]:
        """Default: whatever normalisation already found. Portal adapters override.

        Files are stored only when publicly reachable without authentication (SPEC §16);
        otherwise we keep the deep link and flag `requires_account_for_docs`.
        """
        return list(notice.documents)

    @abstractmethod
    def fetch_since(self, cursor: Cursor) -> Iterator[RawNotice]: ...

    @abstractmethod
    def normalize(self, raw: RawNotice) -> CanonicalNotice: ...


_REGISTRY: dict[str, type[BaseAdapter]] = {}


def register(adapter_cls: type[BaseAdapter]) -> type[BaseAdapter]:
    """Register an adapter family under its `name` so config rows can reference it."""
    _REGISTRY[adapter_cls.name] = adapter_cls
    return adapter_cls


def get_adapter(name: str) -> type[BaseAdapter]:
    if name not in _REGISTRY:
        raise KeyError(f"unknown adapter {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def registered_adapters() -> dict[str, type[BaseAdapter]]:
    return dict(_REGISTRY)
