"""Email connector tests (SPEC §6.5).

The parsing is deliberately narrow - links, and nothing inferred around them - so these tests
are mostly about what the connector *refuses* to conclude. An alert email is prose from a third
party in a format that changes without warning; the failure mode worth guarding is not "missed a
link" but "attributed one org's mail to another", or "invented a title that reads like a fact".
"""

from __future__ import annotations

from bidpilot_ingestion.inbound import (
    MAX_LINKS_PER_EMAIL,
    candidate_title,
    extract_links,
    is_boilerplate,
    link_labels,
    org_id_from_address,
    recognise,
)


class TestAddressing:
    def test_reads_the_org_from_the_tagged_address(self):
        assert org_id_from_address("sources+org_demo_esn@in.bidpilot.example") == "org_demo_esn"

    def test_reads_it_out_of_a_display_name_header(self):
        assert (
            org_id_from_address("BidPilot Sources <sources+org_demo_esn@in.bidpilot.example>")
            == "org_demo_esn"
        )

    def test_is_case_insensitive_on_the_address(self):
        assert org_id_from_address("SOURCES+org_demo_esn@IN.BIDPILOT.EXAMPLE") == "org_demo_esn"

    def test_refuses_an_address_with_no_tag(self):
        # Mail to the bare address cannot be attributed, so it is dropped rather than guessed at.
        assert org_id_from_address("sources@in.bidpilot.example") is None

    def test_refuses_a_different_local_part(self):
        assert org_id_from_address("support+org_demo_esn@in.bidpilot.example") is None

    def test_refuses_a_tag_that_is_not_an_org_id(self):
        assert org_id_from_address("sources+../etc/passwd@in.bidpilot.example") is None
        assert org_id_from_address("sources+someone@in.bidpilot.example") is None

    def test_refuses_empty_and_malformed_input(self):
        assert org_id_from_address("") is None
        assert org_id_from_address("not-an-address") is None


class TestLinkExtraction:
    def test_takes_links_from_a_plain_text_body(self):
        body = "Nouvel avis : https://www.boamp.fr/pages/avis/?idweb=26-12345 - bonne lecture."
        assert extract_links(body, None) == ["https://www.boamp.fr/pages/avis/?idweb=26-12345"]

    def test_drops_the_punctuation_that_ends_the_sentence(self):
        assert extract_links("Voir https://example.com/avis/1.", None) == ["https://example.com/avis/1"]
        assert extract_links("(https://example.com/avis/2)", None) == ["https://example.com/avis/2"]

    def test_prefers_the_href_over_the_visible_label(self):
        html = '<a href="https://ted.europa.eu/en/notice/-/detail/00512345-2026">Voir l\'avis</a>'
        assert extract_links(None, html) == ["https://ted.europa.eu/en/notice/-/detail/00512345-2026"]

    def test_decodes_html_entities_in_a_link(self):
        html = '<a href="https://example.com/a?x=1&amp;y=2">avis</a>'
        assert extract_links(None, html) == ["https://example.com/a?x=1&y=2"]

    def test_keeps_first_occurrence_order_and_drops_repeats(self):
        # Order matters: the first link in an alert is usually the one the sentence is about,
        # and it becomes the candidate's identity.
        text = "https://example.com/b and https://example.com/a and https://example.com/b"
        assert extract_links(text, None) == ["https://example.com/b", "https://example.com/a"]

    def test_ignores_non_http_schemes(self):
        assert extract_links("mailto:x@y.z and ftp://host/file", None) == []

    def test_scans_both_parts_of_a_multipart_message(self):
        found = extract_links(
            "https://example.com/text-only", '<a href="https://example.com/html-only">x</a>'
        )
        assert found == ["https://example.com/text-only", "https://example.com/html-only"]


class TestSourceRecognition:
    def test_recognises_a_ted_notice_url(self):
        found = recognise("https://ted.europa.eu/en/notice/-/detail/00512345-2026")
        assert found is not None
        assert (found.source_code, found.external_id) == ("eu-ted", "00512345-2026")

    def test_recognises_a_ted_url_without_the_detail_segment(self):
        found = recognise("https://ted.europa.eu/en/notice/512345-2026")
        assert found is not None and found.external_id == "512345-2026"

    def test_recognises_a_boamp_url_by_query_parameter(self):
        found = recognise("https://www.boamp.fr/pages/avis/?idweb=26-12345")
        assert found is not None
        assert (found.source_code, found.external_id) == ("fr-boamp", "26-12345")

    def test_recognises_a_boamp_url_by_path(self):
        found = recognise("https://www.boamp.fr/avis/detail/26-98765")
        assert found is not None and found.external_id == "26-98765"

    def test_returns_nothing_for_a_portal_we_do_not_know(self):
        # Not a failure: the long tail is the reason this connector exists. An unrecognised
        # link still becomes a candidate, it just carries no source attribution.
        assert recognise("https://marches.example-hospital.fr/consultation/4412") is None

    def test_does_not_match_a_lookalike_host(self):
        # A forwarded email is third-party content, so a link in it is attacker-influenced. Both
        # shapes below would have passed a naive suffix check: the first appends a domain, the
        # second prepends characters so that `notted.europa.eu` still *ends with* `ted.europa.eu`.
        assert recognise("https://ted.europa.eu.evil.example/en/notice/-/detail/00512345-2026") is None
        assert recognise("https://notted.europa.eu/en/notice/-/detail/00512345-2026") is None
        assert recognise("https://notboamp.fr/pages/avis/?idweb=26-12345") is None

    def test_still_matches_a_real_subdomain(self):
        found = recognise("https://data.ted.europa.eu/en/notice/-/detail/00512345-2026")
        assert found is not None and found.source_code == "eu-ted"


class TestVolumeGuard:
    def test_extraction_itself_does_not_cap(self):
        """Extraction reports everything it found; the cap belongs to the handler.

        Kept separate on purpose, so the number of links skipped can be *recorded* rather than
        quietly lost - a connector that silently drops half a digest is the kind of gap §6.9
        exists to make visible. The capping behaviour is asserted in the pipeline suite.
        """
        body = " ".join(f"https://example.com/avis/{i}" for i in range(MAX_LINKS_PER_EMAIL + 10))
        assert len(extract_links(body, None)) == MAX_LINKS_PER_EMAIL + 10


class TestBoilerplate:
    """An alert email is mostly furniture; only some of it is the announcement."""

    def test_drops_unsubscribe_links_in_both_languages(self):
        assert is_boilerplate("https://portal.example/unsubscribe?u=42")
        assert is_boilerplate("https://portail.example/se-desinscrire?id=9")

    def test_drops_preference_and_legal_pages(self):
        assert is_boilerplate("https://portal.example/email-preferences")
        assert is_boilerplate("https://portail.example/mentions-legales")

    def test_drops_view_in_browser_links(self):
        # Points at the message, not at any consultation announced in it.
        assert is_boilerplate("https://portal.example/newsletter/42")
        assert is_boilerplate("https://portal.example/view-in-browser?id=9")
        assert is_boilerplate("https://portail.example/voir-en-ligne")

    def test_drops_social_buttons(self):
        assert is_boilerplate("https://www.linkedin.com/company/acme")
        assert is_boilerplate("https://x.com/acme")

    def test_keeps_anything_that_looks_like_a_consultation(self):
        # The bias is deliberate: a stray candidate costs one click, a filtered-out tender is
        # the miss this product exists to prevent.
        assert not is_boilerplate("https://marches.example-hospital.fr/consultation/4412")
        assert not is_boilerplate("https://www.boamp.fr/pages/avis/?idweb=26-12345")
        assert not is_boilerplate("https://ted.europa.eu/en/notice/-/detail/00512345-2026")

    def test_does_not_drop_a_notice_whose_host_merely_resembles_a_social_one(self):
        assert not is_boilerplate("https://linkedin.com.marches.example/avis/1")


class TestTitles:
    """A candidate's title has to come from the message, not from an inference about it."""

    def test_prefers_the_portals_own_label_for_the_link(self):
        html = '<a href="https://marches.example.fr/c/1">Infogérance du système d\'information</a>'
        labels = link_labels(html)
        assert labels["https://marches.example.fr/c/1"] == "Infogérance du système d'information"
        assert (
            candidate_title(labels.get("https://marches.example.fr/c/1"), "Alerte", "x", 0, 3)
            == "Infogérance du système d'information"
        )

    def test_strips_markup_and_collapses_whitespace_inside_the_label(self):
        html = '<a href="https://m.example.fr/c/2">  <b>Maintenance</b>\n  multitechnique </a>'
        assert link_labels(html)["https://m.example.fr/c/2"] == "Maintenance multitechnique"

    def test_ignores_a_label_that_names_nothing(self):
        # "Cliquez ici" as a tender title would be worse than the subject line.
        assert link_labels('<a href="https://m.example.fr/c/3">Voir</a>') == {}

    def test_ignores_a_whole_paragraph_used_as_a_label(self):
        long_label = "x" * 400
        assert link_labels(f'<a href="https://m.example.fr/c/4">{long_label}</a>') == {}

    def test_numbers_subject_derived_titles_so_rows_stay_distinguishable(self):
        first = candidate_title(None, "3 nouvelles consultations", "https://a.example/1", 0, 3)
        second = candidate_title(None, "3 nouvelles consultations", "https://a.example/2", 1, 3)
        assert first == "3 nouvelles consultations (1/3)"
        assert second == "3 nouvelles consultations (2/3)"

    def test_does_not_number_a_single_link_message(self):
        assert candidate_title(None, "Nouvel avis", "https://a.example/1", 0, 1) == "Nouvel avis"

    def test_falls_back_to_the_host_when_there_is_no_subject(self):
        assert candidate_title(None, None, "https://marches.example.fr/c/9", 0, 1) == "marches.example.fr"
