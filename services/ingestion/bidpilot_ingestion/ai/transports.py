"""Transports: how the gateway reaches a provider, and how tests reach the gateway.

`AnthropicTransport` is the real one. The other two exist so that everything above them - retry
on schema violation, the breaker, accounting, caching - is tested in CI without a key and
without spending anything, which is the only way those paths get exercised at all.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .gateway import TransportResult

#: EU endpoint pinning, where a provider offers one (§17.4). Read from config rather than
#: hardcoded: which regions exist is a fact that moves, and §24 makes data residency a
#: commitment we should be able to change without a code release.
ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"

DEFAULT_TIMEOUT_SECONDS = 120.0

#: The API version header Anthropic requires. Pinned, because "latest" is not a contract.
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicTransport:
    """Talks to the Messages API.

    Deliberately does no retrying, no caching and no accounting: those belong to the gateway, and
    a transport that quietly retried would make the breaker's failure count meaningless.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key or os.environ.get(ANTHROPIC_API_KEY_ENV)
        if not self._api_key:
            raise RuntimeError(
                f"{ANTHROPIC_API_KEY_ENV} is not set. The gateway will not run without a key; "
                "use ScriptedTransport in tests and RecordedTransport for replay."
            )
        self._base_url = (
            base_url or os.environ.get(ANTHROPIC_BASE_URL_ENV) or "https://api.anthropic.com"
        ).rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int, temperature: float
    ) -> TransportResult:
        response = self._client.post(
            f"{self._base_url}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        response.raise_for_status()
        body = response.json()

        text = "".join(
            block.get("text", "") for block in body.get("content", []) if block.get("type") == "text"
        )
        usage = body.get("usage", {})
        return TransportResult(
            text=text,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
        )


@dataclass
class ScriptedTransport:
    """Returns prepared answers in order, and records what it was asked.

    The point of the recording is that a test can assert the *second* attempt differed from the
    first - which is the whole behaviour of schema retry, and is invisible if you only check the
    final result.
    """

    responses: Iterable[str | Exception]
    tokens_in: int = 100
    tokens_out: int = 50
    calls: list[dict[str, Any]] = field(default_factory=list)
    _iterator: Iterator[str | Exception] | None = None

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int, temperature: float
    ) -> TransportResult:
        if self._iterator is None:
            self._iterator = iter(self.responses)
        self.calls.append(
            {
                "model": model,
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        try:
            nxt = next(self._iterator)
        except StopIteration:  # pragma: no cover - a test that runs out has a bug in it
            raise AssertionError("ScriptedTransport ran out of responses") from None
        if isinstance(nxt, Exception):
            raise nxt
        return TransportResult(text=nxt, tokens_in=self.tokens_in, tokens_out=self.tokens_out)


class RecordedTransport:
    """Replays answers captured from a real run, keyed by prompt content.

    This is how the eval harness can run without a key once someone has recorded a pass, and it
    follows ADR-0001's rule for fixtures: captured from the real thing, never authored. A
    cassette is a record of what a model actually said, so a replayed eval measures a real
    response rather than one written to pass.
    """

    def __init__(self, cassette: Path) -> None:
        self._cassette = cassette
        self._entries: dict[str, dict[str, Any]] = {}
        if cassette.exists():
            self._entries = json.loads(cassette.read_text(encoding="utf-8"))

    @staticmethod
    def key(model: str, system: str, user: str) -> str:
        import hashlib

        digest = hashlib.sha256()
        for part in (model, system, user):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int, temperature: float
    ) -> TransportResult:
        entry = self._entries.get(self.key(model, system, user))
        if entry is None:
            raise KeyError(
                "no recorded response for this prompt. Re-record the cassette against the live "
                f"provider: {self._cassette}"
            )
        return TransportResult(
            text=entry["text"],
            tokens_in=int(entry.get("tokens_in", 0)),
            tokens_out=int(entry.get("tokens_out", 0)),
        )

    def record(self, *, model: str, system: str, user: str, result: TransportResult) -> None:
        self._entries[self.key(model, system, user)] = {
            "text": result.text,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        }

    def save(self) -> None:
        self._cassette.parent.mkdir(parents=True, exist_ok=True)
        self._cassette.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
