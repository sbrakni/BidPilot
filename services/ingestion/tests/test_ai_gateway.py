"""LLM gateway tests (SPEC §17.4).

Every behaviour here is one the gateway exists to provide, and none of them can be observed from
a call's happy path: a schema retry looks identical to a first-attempt success unless you inspect
what was sent, and a breaker that never opens looks exactly like one that works.

No provider key is needed, on purpose. Paths that only run during an incident - the second
attempt, the open circuit, the fallback tier - are precisely the ones that must be tested rather
than trusted, so the transport is injected and scripted.
"""

from __future__ import annotations

import json

import pytest
from bidpilot_ingestion.ai import (
    CircuitOpenError,
    Gateway,
    GatewayError,
    PromptSpec,
    SchemaValidationError,
    ScriptedTransport,
)
from bidpilot_ingestion.ai.cache import MemoryStore, ResponseCache
from bidpilot_ingestion.ai.gateway import CIRCUIT_FAILURE_THRESHOLD, cache_key
from bidpilot_ingestion.ai.registry import available_prompts, load_prompt, resolve_model
from bidpilot_ingestion.ai.schema import SchemaViolation, validate_against

SIMPLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string", "minLength": 1}},
}


def _prompt(tier="M", schema=None) -> PromptSpec:
    return PromptSpec(
        id="test_prompt",
        version="v1",
        system="You answer in JSON.",
        schema=schema or SIMPLE_SCHEMA,
        tier=tier,
        max_tokens=256,
        temperature=0.0,
        description="test",
    )


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_TIER_S", "model-small")
    monkeypatch.setenv("LLM_MODEL_TIER_M", "model-medium")
    monkeypatch.setenv("LLM_MODEL_TIER_L", "model-large")


class TestTierResolution:
    def test_a_tier_resolves_to_the_configured_model(self):
        assert resolve_model("M") == "model-medium"

    def test_an_unconfigured_tier_raises_rather_than_guessing(self, monkeypatch):
        # Silently substituting a model would mean an analysis ran on something other than what
        # the eval gates measured (Annex E.4).
        monkeypatch.delenv("LLM_MODEL_TIER_L")
        with pytest.raises(RuntimeError, match="LLM_MODEL_TIER_L"):
            resolve_model("L")


class TestSchemaEnforcement:
    def test_accepts_valid_output(self):
        assert validate_against(SIMPLE_SCHEMA, '{"answer": "yes"}') == {"answer": "yes"}

    def test_strips_a_markdown_fence(self):
        # The single most common way a correct answer arrives unusable.
        assert validate_against(SIMPLE_SCHEMA, '```json\n{"answer": "yes"}\n```') == {"answer": "yes"}

    def test_rejects_a_missing_required_field_by_name(self):
        with pytest.raises(SchemaViolation, match="answer"):
            validate_against(SIMPLE_SCHEMA, "{}")

    def test_rejects_an_unexpected_field(self):
        with pytest.raises(SchemaViolation, match="not in the schema"):
            validate_against(SIMPLE_SCHEMA, '{"answer": "yes", "confidence": 0.9}')

    def test_rejects_a_boolean_where_a_number_belongs(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        with pytest.raises(SchemaViolation, match="boolean"):
            validate_against(schema, '{"n": true}')

    def test_does_not_repair_malformed_json(self):
        # A guessed comma is a guessed fact; the retry path exists so this can be rejected.
        with pytest.raises(SchemaViolation, match="not valid JSON"):
            validate_against(SIMPLE_SCHEMA, '{"answer": "yes",}')

    def test_refuses_to_ignore_a_keyword_it_does_not_implement(self):
        # A silently ignored keyword is a contract silently not enforced.
        with pytest.raises(NotImplementedError, match="unsupported"):
            validate_against({"type": "object", "oneOf": []}, "{}")

    def test_nullable_permits_null_but_type_still_applies(self):
        schema = {"type": "object", "properties": {"x": {"type": "string", "nullable": True}}}
        assert validate_against(schema, '{"x": null}') == {"x": None}
        with pytest.raises(SchemaViolation):
            validate_against(schema, '{"x": 3}')


class TestRetryOnSchemaViolation:
    def test_a_schema_violation_is_retried_with_the_error_stated(self):
        transport = ScriptedTransport(responses=['{"wrong": 1}', '{"answer": "second try"}'])
        gateway = Gateway(transport=transport)

        response = gateway.complete(_prompt(), "Question?")

        assert response.data == {"answer": "second try"}
        assert response.attempts == 2
        # The retry has to *differ* from the first attempt, or it is just asking again and
        # hoping. The violation is named in the follow-up.
        assert "did not match the required JSON schema" in transport.calls[1]["user"]
        assert "answer" in transport.calls[1]["user"]

    def test_it_gives_up_rather_than_retrying_forever(self):
        transport = ScriptedTransport(responses=["nope", "still nope", "nope again", "and again"])
        gateway = Gateway(transport=transport)

        with pytest.raises(SchemaValidationError):
            gateway.complete(_prompt(), "Question?")
        assert len(transport.calls) == 3, "one attempt plus two retries, then stop"

    def test_a_first_attempt_success_makes_no_second_call(self):
        transport = ScriptedTransport(responses=['{"answer": "ok"}'])
        Gateway(transport=transport).complete(_prompt(), "Question?")
        assert len(transport.calls) == 1


class TestCaching:
    def test_an_identical_call_is_served_from_cache(self):
        transport = ScriptedTransport(responses=['{"answer": "computed once"}'])
        gateway = Gateway(transport=transport)

        first = gateway.complete(_prompt(), "Same question")
        second = gateway.complete(_prompt(), "Same question")

        assert first.data == second.data
        assert first.cached is False and second.cached is True
        assert len(transport.calls) == 1, "the second call must not reach the provider"
        # A cache hit costs nothing and says so, so cost and volume stay separate questions.
        assert second.tokens_in == 0 and second.tokens_out == 0

    def test_a_different_input_is_not_a_hit(self):
        transport = ScriptedTransport(responses=['{"answer": "a"}', '{"answer": "b"}'])
        gateway = Gateway(transport=transport)
        gateway.complete(_prompt(), "Question one")
        gateway.complete(_prompt(), "Question two")
        assert len(transport.calls) == 2

    def test_a_new_prompt_version_invalidates_the_cache(self):
        """Otherwise an eval run would score answers produced by the prompt it replaced."""
        v1 = _prompt()
        v2 = PromptSpec(**{**v1.__dict__, "version": "v2"})
        assert cache_key(v1, "x", "model-medium") != cache_key(v2, "x", "model-medium")

    def test_the_model_is_part_of_the_key(self):
        prompt = _prompt()
        assert cache_key(prompt, "x", "model-medium") != cache_key(prompt, "x", "model-small")

    def test_entries_expire(self):
        # One tick per call: `set` stamps the expiry at t=0, `get` asks at t=10_000.
        clock = iter([0.0, 10_000.0])
        store = MemoryStore(clock=lambda: next(clock))
        cache = ResponseCache(store=store, ttl_seconds=60)
        cache.set("k", {"a": 1})
        assert cache.get("k") is None

    def test_an_entry_inside_its_ttl_is_still_served(self):
        clock = iter([0.0, 30.0])
        store = MemoryStore(clock=lambda: next(clock))
        cache = ResponseCache(store=store, ttl_seconds=60)
        cache.set("k", {"a": 1})
        assert cache.get("k") == {"a": 1}

    def test_the_store_is_bounded(self):
        """A long-running worker must not accumulate every answer it has ever seen."""
        store = MemoryStore(max_entries=2, clock=lambda: 0.0)
        for index in range(5):
            store.set(f"k{index}", {"i": index}, ttl_seconds=60)
        surviving = [key for key in ("k0", "k1", "k2", "k3", "k4") if store.get(key) is not None]
        assert surviving == ["k3", "k4"], "least-recently-used entries are evicted"


class TestCircuitBreaker:
    def test_it_opens_after_repeated_transport_failures(self):
        transport = ScriptedTransport(responses=[RuntimeError("provider down")] * CIRCUIT_FAILURE_THRESHOLD)
        gateway = Gateway(transport=transport)
        prompt = _prompt(tier="S")  # S has no fallback, so the breaker's own behaviour shows.

        for index in range(CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(GatewayError):
                gateway.complete(prompt, f"question {index}", allow_cache=False)

        with pytest.raises(CircuitOpenError):
            gateway.complete(prompt, "one more", allow_cache=False)
        assert len(transport.calls) == CIRCUIT_FAILURE_THRESHOLD, (
            "once open, the provider must not be called at all"
        )

    def test_an_open_circuit_falls_back_to_a_cheaper_tier(self):
        """Down, never up: a worse answer is visible, a surprise bill is not."""
        transport = ScriptedTransport(
            responses=[*([RuntimeError("down")] * CIRCUIT_FAILURE_THRESHOLD), '{"answer": "from fallback"}']
        )
        gateway = Gateway(transport=transport)
        prompt = _prompt(tier="L")

        for index in range(CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(GatewayError):
                gateway.complete(prompt, f"q{index}", allow_cache=False)

        response = gateway.complete(prompt, "after the breaker opened", allow_cache=False)
        assert response.data == {"answer": "from fallback"}
        assert response.model == "model-medium", "L fell back to M"

    def test_a_success_resets_the_failure_count(self):
        transport = ScriptedTransport(responses=[RuntimeError("blip"), '{"answer": "fine"}'])
        gateway = Gateway(transport=transport)
        prompt = _prompt(tier="S")

        with pytest.raises(GatewayError):
            gateway.complete(prompt, "q1", allow_cache=False)
        gateway.complete(prompt, "q2", allow_cache=False)

        # A single blip must not leave the breaker one failure from tripping.
        assert gateway._circuits["model-small"].failures == 0


class TestPromptRegistry:
    def test_the_registry_contains_the_extraction_prompt(self):
        assert "extract_admin" in available_prompts()

    def test_a_prompt_loads_with_its_schema_and_settings(self):
        prompt = load_prompt("extract_admin", "v1")
        assert prompt.ref == "extract_admin@v1"
        assert prompt.temperature == 0.0, "extraction must be reproducible or the eval is noise"
        assert prompt.schema["type"] == "object"

    def test_latest_resolves_to_a_concrete_version(self):
        assert load_prompt("extract_admin").version.startswith("v")

    def test_the_extraction_prompt_obeys_annex_e2(self):
        """E.2 rule 1: verbatim quotes plus pages, and "not found" must be allowed."""
        prompt = load_prompt("extract_admin", "v1")
        system = prompt.system.lower()
        assert "verbatim" in system
        assert "page" in system
        assert "not found" in system or "null" in system
        # Every quoted fact carries its page, enforced by the schema rather than by the prose.
        criteria_item = prompt.schema["properties"]["award_criteria"]["items"]
        assert set(criteria_item["required"]) >= {"page", "quote"}

    def test_an_unknown_prompt_raises(self):
        with pytest.raises(LookupError):
            load_prompt("no_such_prompt")


class TestRecordedOutputStaysHonest:
    def test_the_gateway_returns_what_the_model_said_not_a_repair(self):
        """The gateway validates; it never edits. An answer that passes is the model's own."""
        payload = {"answer": "  spaced  "}
        transport = ScriptedTransport(responses=[json.dumps(payload)])
        response = Gateway(transport=transport).complete(_prompt(), "q")
        assert response.data == payload
