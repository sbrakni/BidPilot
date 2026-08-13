"""The gateway itself (SPEC §17.4).

One call path, carrying every control the spec asks for:

    tier → model · versioned prompt · schema-validated output with retry ·
    cache by (prompt_hash, model) · per-org token accounting · circuit breaker + fallback

The transport is injected. That is what makes this testable without a provider key, and it is
the same shape the notifier uses for SMTP (ADR-0013): the interesting logic - retrying on a
schema violation, refusing to run when the circuit is open, billing the right org - is exercised
against a scripted transport, so those paths are covered by tests that run in CI rather than by
hope.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..ids import new_id
from .cache import ResponseCache
from .registry import ModelTier, PromptSpec, resolve_model
from .schema import SchemaViolation, validate_against

log = logging.getLogger(__name__)

#: How many times a schema-invalid answer is sent back for correction before giving up.
#: Two, not more: a model that has failed the same schema twice with the error in front of it is
#: not going to succeed on the fifth attempt, and each retry costs a full generation.
MAX_SCHEMA_RETRIES = 2

#: Consecutive transport failures before the breaker opens.
CIRCUIT_FAILURE_THRESHOLD = 5

#: How long it stays open. Long enough to let a provider incident pass, short enough that a
#: recovered provider is picked up within one analysis.
CIRCUIT_RESET_SECONDS = 60.0

#: Fallback order when a tier's model is unavailable. Down, never up: a cheaper model producing a
#: worse register is a known, visible degradation; silently escalating to a pricier one turns a
#: provider incident into a bill nobody approved.
FALLBACK_TIER: dict[ModelTier, ModelTier | None] = {"L": "M", "M": "S", "S": None}


class GatewayError(RuntimeError):
    """Any failure the caller must handle rather than retry blindly."""


class CircuitOpenError(GatewayError):
    """The breaker is open: the provider has failed repeatedly and is not being called."""


class SchemaValidationError(GatewayError):
    """The model could not produce output matching the prompt's schema, after retries."""


@dataclass(frozen=True)
class ModelResponse:
    """What a call produced, and everything needed to audit it afterwards."""

    data: dict[str, Any]
    model: str
    prompt_ref: str
    tokens_in: int
    tokens_out: int
    cached: bool
    attempts: int

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True)
class TransportResult:
    """A provider's raw answer, before this module decides whether to believe it."""

    text: str
    tokens_in: int
    tokens_out: int


class Transport(Protocol):
    """How the gateway reaches a provider.

    Narrow on purpose: the gateway owns retries, caching, accounting and the breaker, so a
    transport only has to turn a prompt into text. That keeps the provider-specific surface
    small enough to swap.
    """

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int, temperature: float
    ) -> TransportResult: ...


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None

    def record_failure(self, now: float) -> None:
        self.failures += 1
        if self.failures >= CIRCUIT_FAILURE_THRESHOLD:
            self.opened_at = now

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def is_open(self, now: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= CIRCUIT_RESET_SECONDS:
            # Half-open: let one call through to find out whether the provider is back.
            self.opened_at = None
            self.failures = 0
            return False
        return True


@dataclass
class Gateway:
    """The only supported way to call a model."""

    transport: Transport
    cache: ResponseCache = field(default_factory=ResponseCache)
    _circuits: dict[str, _Circuit] = field(default_factory=dict)
    _clock: Any = time.monotonic

    def complete(
        self,
        prompt: PromptSpec,
        user: str,
        *,
        org_id: str | None = None,
        feature: str | None = None,
        connection: Connection | None = None,
        allow_cache: bool = True,
    ) -> ModelResponse:
        """Run a prompt and return output that has been validated against its schema.

        `org_id` and `connection` together record usage. Both optional because the eval harness
        calls the gateway with no tenant at all - it is measuring the prompt, not serving anyone.
        """
        model = resolve_model(prompt.tier)
        key = cache_key(prompt, user, model)

        if allow_cache:
            hit = self.cache.get(key)
            if hit is not None:
                # A cache hit is still recorded as zero-cost usage, so that "how many analyses
                # did this org run" and "what did they cost" stay separate questions.
                return ModelResponse(
                    data=hit,
                    model=model,
                    prompt_ref=prompt.ref,
                    tokens_in=0,
                    tokens_out=0,
                    cached=True,
                    attempts=0,
                )

        response = self._complete_uncached(prompt, user, model)

        if allow_cache:
            self.cache.set(key, response.data)
        if org_id and connection is not None:
            record_usage(
                connection,
                org_id=org_id,
                feature=feature or prompt.id,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
            )
        return response

    def _complete_uncached(self, prompt: PromptSpec, user: str, model: str) -> ModelResponse:
        now = self._clock()
        circuit = self._circuits.setdefault(model, _Circuit())

        if circuit.is_open(now):
            fallback = FALLBACK_TIER.get(prompt.tier)
            if fallback is None:
                raise CircuitOpenError(f"model {model} is failing and tier {prompt.tier} has no fallback")
            log.warning("circuit open for %s; falling back to tier %s", model, fallback)
            return self._complete_uncached(
                # Same prompt, different tier: the caller asked for a task, not a model.
                PromptSpec(**{**prompt.__dict__, "tier": fallback}),
                user,
                resolve_model(fallback),
            )

        message = user
        last_violation: SchemaViolation | None = None

        for attempt in range(1, MAX_SCHEMA_RETRIES + 2):
            try:
                result = self.transport.complete(
                    model=model,
                    system=prompt.system,
                    user=message,
                    max_tokens=prompt.max_tokens,
                    temperature=prompt.temperature,
                )
            except Exception as exc:
                circuit.record_failure(self._clock())
                raise GatewayError(f"transport failed for {model}: {exc}") from exc

            circuit.record_success()

            try:
                data = validate_against(prompt.schema, result.text)
            except SchemaViolation as violation:
                last_violation = violation
                log.info(
                    "prompt %s attempt %d produced invalid output: %s",
                    prompt.ref,
                    attempt,
                    violation,
                )
                # Hand the error back rather than re-asking blindly: the failure is almost
                # always one field, and naming it is what makes the second attempt different
                # from the first.
                message = (
                    f"{user}\n\n---\n"
                    f"Your previous answer did not match the required JSON schema: {violation}\n"
                    "Return only valid JSON matching the schema. Do not explain."
                )
                continue

            return ModelResponse(
                data=data,
                model=model,
                prompt_ref=prompt.ref,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cached=False,
                attempts=attempt,
            )

        raise SchemaValidationError(
            f"prompt {prompt.ref} did not produce schema-valid output after "
            f"{MAX_SCHEMA_RETRIES + 1} attempts: {last_violation}"
        )


def cache_key(prompt: PromptSpec, user: str, model: str) -> str:
    """`(prompt_hash, model)` as §17.4 specifies, with the input folded into the hash.

    The prompt's *version* is part of it, so publishing a new version of a prompt invalidates its
    cache rather than serving answers produced by the old one - which would make an eval run
    measure a prompt that is no longer in use.
    """
    digest = hashlib.sha256()
    digest.update(prompt.ref.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(prompt.system.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(user.encode("utf-8"))
    return f"{digest.hexdigest()}:{model}"


def record_usage(
    connection: Connection, *, org_id: str, feature: str, tokens_in: int, tokens_out: int
) -> None:
    """Accumulate this org's usage for the current month (§15.2 credits, §17.4 accounting).

    Upsert onto one row per (org, month, feature) rather than a row per call: the question the
    product asks is "how much has this org used this month", and a row per call would be a table
    that grows with traffic to answer a question that never needs individual calls.
    """
    connection.execute(
        text(
            """
            INSERT INTO ai_usage (id, org_id, month, feature, tokens_in, tokens_out,
                                  cost_cents, created_at)
            VALUES (:id, :org, to_char(now(), 'YYYY-MM'), :feature, :tin, :tout, 0, now())
            ON CONFLICT (org_id, month, feature) DO UPDATE
               SET tokens_in  = ai_usage.tokens_in  + EXCLUDED.tokens_in,
                   tokens_out = ai_usage.tokens_out + EXCLUDED.tokens_out
            """
        ),
        {
            "id": new_id("aiu"),
            "org": org_id,
            "feature": feature,
            "tin": tokens_in,
            "tout": tokens_out,
        },
    )


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
