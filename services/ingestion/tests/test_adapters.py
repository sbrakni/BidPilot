"""Adapter regression tests against real captured payloads (SPEC §21.1).

These are the tests that catch a source changing shape - the failure mode §23 rates as
High risk. They assert on invariants of the *canonical* output, not on the incidental
content of any one notice, so refreshing the fixtures does not rewrite the suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from bidpilot_ingestion.adapters import LegalBasis, SourceConfig, TedAdapter, get_adapter
from bidpilot_ingestion.adapters.base import BaseAdapter, Cursor, RateLimiter, content_hash
from bidpilot_ingestion.adapters.boamp import clean_text, dept_to_nuts
from bidpilot_ingestion.adapters.ted import _combine_date_time, _parse_offset_date
from bidpilot_ingestion.canonical import CanonicalNotice, NoticeType

from tests.conftest import boamp_raws, ted_raws


# ---------------------------------------------------------------- canonical invariants


def _assert_canonical_invariants(notice: CanonicalNotice) -> None:
    assert notice.title.strip(), "a notice without a title is unusable in the inbox"
    assert len(notice.country) == 2 and notice.country.isupper()
    assert notice.source_refs, "provenance requires at least one source reference"
    assert notice.provenance.adapter and "@" in notice.provenance.adapter
    assert all(len(code) == 8 and code.isdigit() for code in notice.cpv)
    assert len(set(notice.cpv)) == len(notice.cpv), "CPV codes must be deduplicated"
    # A 3-letter alpha token is an ISO-3166 country, which TED mixes into NUTS arrays.
    assert not any(len(code) == 3 and code.isalpha() for code in notice.nuts)
    for stamp in (notice.dates.published_at, notice.dates.deadline_at):
        if stamp is not None:
            assert stamp.tzinfo is not None, "instants must be timezone-aware (SPEC §16)"


@pytest.mark.parametrize("fixture_name", ["ted_competition", "ted_award", "ted_planning"])
def test_ted_normalizes_every_fixture_notice(ted_adapter, fixture_name):
    raws = ted_raws(fixture_name)
    assert raws, f"fixture {fixture_name} is empty - re-capture it"
    for raw in raws:
        _assert_canonical_invariants(ted_adapter.normalize(raw))


@pytest.mark.parametrize("fixture_name", ["boamp_recent", "boamp_awards"])
def test_boamp_normalizes_every_fixture_record(boamp_adapter, fixture_name):
    raws = boamp_raws(fixture_name)
    assert raws, f"fixture {fixture_name} is empty - re-capture it"
    for raw in raws:
        notice = boamp_adapter.normalize(raw)
        _assert_canonical_invariants(notice)
        assert notice.country == "FR"
        assert notice.dates.tz == "Europe/Paris"


def test_no_unmapped_source_fields(ted_adapter, boamp_adapter):
    """An unmapped key means the source grew a field we are silently ignoring.

    This is the early-warning signal for §6.1's "extraction yield dropped" health check:
    it fires on the *shape* change, before the yield drop is measurable.
    """
    for raw in ted_raws():
        assert ted_adapter.normalize(raw).provenance.unmapped_fields == []
    for raw in boamp_raws():
        assert boamp_adapter.normalize(raw).provenance.unmapped_fields == []


# ---------------------------------------------------------------- notice typing


def test_ted_distinguishes_notice_types(ted_adapter):
    """Planning / competition / result must not be conflated: §14.2's renewal radar reads
    award notices, and §8's inbox must not surface an already-awarded tender as biddable.
    """
    kinds = {
        name: {ted_adapter.normalize(raw).notice_type for raw in ted_raws(name)}
        for name in ("ted_competition", "ted_award", "ted_planning")
    }
    assert kinds["ted_competition"] == {NoticeType.COMPETITION}
    assert kinds["ted_award"] == {NoticeType.RESULT}
    assert kinds["ted_planning"] == {NoticeType.PLANNING}


def test_boamp_award_records_carry_a_winner(boamp_adapter):
    """`titulaire` is the renewal radar's raw material (SPEC §14.2)."""
    notices = [boamp_adapter.normalize(raw) for raw in boamp_raws("boamp_awards")]
    results = [n for n in notices if n.notice_type is NoticeType.RESULT]
    assert results, "the awards fixture should contain result notices"
    assert all(n.award and n.award.supplier_name for n in results)
    # BOAMP does not publish the winner's SIREN; inventing one would mis-attribute wins.
    assert all(n.award.supplier_siren is None for n in results)


# ---------------------------------------------------------------- title & text quality


def test_ted_titles_are_descriptive_not_internal_references(ted_adapter):
    """`title-lot` is often a buyer's internal reference ("WS2848982494 - 1").

    Those would make the inbox unreadable, so the adapter falls back to the composed
    `notice-title` with TED's "<Country> – <CPV label> – " prefix stripped.
    """
    titles = [ted_adapter.normalize(raw).title for raw in ted_raws("ted_award")]
    assert titles
    for title in titles:
        assert len(title) >= 15, f"title too short to be meaningful: {title!r}"
        assert " " in title, f"title looks like a bare reference: {title!r}"
    # The country/CPV prefix must not survive into the canonical title.
    assert not any(title.startswith(("France – ", "Belgique – ", "Luxembourg – ")) for title in titles)


def test_ted_detects_document_language(ted_adapter):
    languages = {ted_adapter.normalize(raw).language for raw in ted_raws()}
    assert languages - {None}, "expected at least one detected language"
    assert all(lang is None or len(lang) == 2 for lang in languages)


def test_boamp_decodes_html_entities(boamp_adapter):
    """BOAMP serves entity-encoded text; raw entities must never reach the UI or the LLM."""
    for name in ("boamp_recent", "boamp_awards"):
        for raw in boamp_raws(name):
            notice = boamp_adapter.normalize(raw)
            blob = f"{notice.title} {notice.buyer.name or ''} {notice.description or ''}"
            assert "&#" not in blob and "&amp;" not in blob and "&quot;" not in blob


def test_clean_text_handles_double_encoding():
    assert clean_text("Communaut&#233; d&#039;Agglom&#233;ration") == "Communauté d'Agglomération"
    assert clean_text("&amp;#039;") == "'"
    assert clean_text("  spaced   out  ") == "spaced out"
    assert clean_text(None) is None
    assert clean_text("") is None


# ---------------------------------------------------------------- geography


def test_boamp_maps_departements_to_nuts(boamp_adapter):
    """Without this bridge every BOAMP notice fails a NUTS-based hard filter (SPEC §8.1)."""
    assert dept_to_nuts("75") == "FR10"
    assert dept_to_nuts("2A") == "FRM0"
    assert dept_to_nuts("974") == "FRY4"
    assert dept_to_nuts("6") == "FRL0", "single-digit codes arrive unpadded"
    assert dept_to_nuts("99") is None, "an unknown code must not be guessed"

    notices = [boamp_adapter.normalize(raw) for raw in boamp_raws()]
    assert any(notice.nuts for notice in notices), "expected NUTS on at least one record"
    assert all(all(code.startswith(("FR", "BE", "LU", "CH", "DE", "ES", "IT")) for code in n.nuts) for n in notices)


# ---------------------------------------------------------------- date handling


def test_parse_offset_date_handles_ted_shapes():
    """TED sends `2026-08-11+02:00` - a date carrying an offset, not an instant."""
    parsed = _parse_offset_date("2026-08-11+02:00")
    assert parsed == datetime(2026, 8, 10, 22, 0, tzinfo=UTC)
    assert _parse_offset_date(["2026-08-11+02:00"]) == parsed
    assert _parse_offset_date("2026-08-11T09:30:00Z") == datetime(2026, 8, 11, 9, 30, tzinfo=UTC)
    assert _parse_offset_date("not a date") is None
    assert _parse_offset_date(None) is None


def test_combine_date_time_uses_the_buyers_clock():
    """A submission deadline is defined in the buyer's timezone (SPEC §5), so the offset
    on the time component governs - 16:30+02:00 is 14:30 UTC, not 16:30 UTC."""
    combined = _combine_date_time(["2026-09-16+02:00"], ["16:30:00+02:00"])
    assert combined == datetime(2026, 9, 16, 14, 30, tzinfo=UTC)
    assert _combine_date_time(["2026-09-16+02:00"], None) is None
    assert _combine_date_time(None, ["16:30:00+02:00"]) is None


def test_ted_competition_notices_have_deadlines(ted_adapter):
    """P3: deadlines are sacred. A competition notice without one cannot be triaged."""
    notices = [ted_adapter.normalize(raw) for raw in ted_raws()]
    with_deadline = [n for n in notices if n.dates.deadline_at is not None]
    assert len(with_deadline) >= len(notices) * 0.8, "most competition notices must carry a deadline"


# ---------------------------------------------------------------- SDK behaviours


def test_adapter_refuses_to_run_without_legal_basis():
    """SPEC §24.7: the registry field is mandatory and the adapter enforces it."""
    with pytest.raises(ValueError, match="legal basis"):
        TedAdapter(SourceConfig(code="eu-ted", country="EU", tier=1, kind="api", legal=None))


def test_invalid_legal_basis_is_rejected():
    with pytest.raises(ValueError, match="legal basis must be one of"):
        TedAdapter(
            SourceConfig(code="x", country="EU", tier=1, kind="api", legal=LegalBasis(basis="because-i-said-so"))
        )


def test_adapters_are_registered_by_name():
    assert get_adapter("ted") is TedAdapter
    with pytest.raises(KeyError):
        get_adapter("nope")


def test_content_hash_is_key_order_independent():
    """Otherwise every re-fetch would look like an amendment and spam the team (SPEC §6.7)."""
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test_rate_limiter_spaces_requests():
    """Politeness is a legal posture (SPEC §16), so it is enforced, not advisory."""
    slept: list[float] = []
    clock = iter([0.0, 0.0, 0.1, 0.1])
    limiter = RateLimiter(max_rps=1.0)
    limiter.wait(sleep=slept.append, now=lambda: next(clock))
    limiter.wait(sleep=slept.append, now=lambda: next(clock))
    assert slept and slept[-1] == pytest.approx(0.9, abs=0.01)


def test_request_retries_transient_failures_then_succeeds():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    adapter = TedAdapter(
        SourceConfig(code="eu-ted", country="EU", tier=1, kind="api", legal=LegalBasis(basis="open-license")),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    response = adapter.request("GET", "https://example.test/x", sleep=lambda _: None)
    assert response.json() == {"ok": True}
    assert len(attempts) == 3


def test_request_gives_up_after_max_retries():
    adapter = TedAdapter(
        SourceConfig(
            code="eu-ted",
            country="EU",
            tier=1,
            kind="api",
            max_retries=2,
            legal=LegalBasis(basis="open-license"),
        ),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
    )
    with pytest.raises(httpx.HTTPStatusError):
        adapter.request("GET", "https://example.test/x", sleep=lambda _: None)


def test_fetch_since_pages_until_short_page():
    """A page shorter than the limit ends the window; anything else loops forever."""
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = len(pages) + 1
        pages.append(page)
        count = TedAdapter.PAGE_SIZE if page == 1 else 3
        notices = [{"publication-number": f"{page}-{i}-2026"} for i in range(count)]
        return httpx.Response(200, json={"notices": notices, "totalNoticeCount": count})

    adapter = TedAdapter(
        SourceConfig(code="eu-ted", country="EU", tier=1, kind="api", max_rps=0, legal=LegalBasis(basis="open-license")),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    fetched = list(adapter.fetch_since(Cursor()))
    assert len(pages) == 2
    assert len(fetched) == TedAdapter.PAGE_SIZE + 3
    assert all(raw.content_hash for raw in fetched), "raw payloads are hashed on fetch"


def test_notices_without_a_stable_id_are_skipped():
    """Without an external id we can neither dedupe nor re-fetch, so we drop rather than
    fabricate an id that would create a duplicate card on the next run."""
    handler = lambda r: httpx.Response(  # noqa: E731 - single-expression test stub
        200, json={"notices": [{"notice-title": {"fra": "sans id"}}], "totalNoticeCount": 1}
    )
    adapter = TedAdapter(
        SourceConfig(code="eu-ted", country="EU", tier=1, kind="api", max_rps=0, legal=LegalBasis(basis="open-license")),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert list(adapter.fetch_since(Cursor())) == []


def test_base_adapter_cannot_be_instantiated_without_the_contract():
    """A half-implemented adapter must fail at import/instantiation, not at 3am in prod."""

    class Incomplete(BaseAdapter):
        name = "incomplete"
        version = "0.0.1"

    with pytest.raises(TypeError):
        Incomplete(SourceConfig(code="x", country="FR", tier=3, kind="scrape", legal=LegalBasis(basis="robots-ok")))
