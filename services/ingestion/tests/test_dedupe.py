"""Clustering tests (SPEC §6.7) including the F1 acceptance criterion from §6.10:

    "Given TED + BOAMP adapters enabled, when a notice is published on BOAMP that also
     exists on TED, then BidPilot shows exactly one card with both source badges."

The cross-source pair is built from a *real* TED notice plus the BOAMP-shaped publication
of the same tender, because that is the case the product must not get wrong: showing one
tender twice erodes trust in the whole feed, and merging two different tenders hides one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bidpilot_ingestion.canonical import (
    Amounts,
    Buyer,
    CanonicalNotice,
    Dates,
    DocumentKind,
    DocumentRef,
    NoticeType,
    Procedure,
    Provenance,
    SourceRef,
    Urls,
)
from bidpilot_ingestion.dedupe import (
    MatchReason,
    cluster_notices,
    merge,
    normalize_buyer_name,
    same_tender,
    trigram_similarity,
)

DEADLINE = datetime(2026, 9, 16, 14, 30, tzinfo=UTC)


def make_notice(
    *,
    source: str,
    external_id: str,
    title: str,
    buyer_name: str | None = "Commune d'Hyères",
    buyer_siren: str | None = None,
    deadline: datetime | None = DEADLINE,
    published_at: datetime | None = None,
    notice_type: NoticeType = NoticeType.COMPETITION,
    country: str = "FR",
    description: str | None = None,
    cpv: list[str] | None = None,
    nuts: list[str] | None = None,
    amount: float | None = None,
    documents: list[DocumentRef] | None = None,
) -> CanonicalNotice:
    return CanonicalNotice(
        source_refs=[
            SourceRef(source=source, external_id=external_id, url=f"https://{source}.test/{external_id}")
        ],
        notice_type=notice_type,
        country=country,
        buyer=Buyer(name=buyer_name, siren=buyer_siren),
        title=title,
        description=description,
        cpv=cpv or ["31600000"],
        nuts=nuts or ["FRL05"],
        procedure=Procedure(),
        amounts=Amounts(estimated_total=amount),
        dates=Dates(deadline_at=deadline, published_at=published_at),
        documents=documents or [],
        urls=Urls(notice=f"https://{source}.test/{external_id}"),
        provenance=Provenance(adapter=f"{source}@1.0.0", normalized_at=datetime.now(UTC)),
    )


# ---------------------------------------------------------------- acceptance criterion


def test_same_tender_on_ted_and_boamp_yields_one_cluster_with_both_badges():
    """§6.10: exactly one card, both source badges."""
    ted = make_notice(
        source="eu-ted",
        external_id="553797-2026",
        title="EVENEMENTIEL - Location, montage et démontage de matériel pour l'alimentation électrique",
        published_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    boamp = make_notice(
        source="fr-boamp",
        external_id="26-79589",
        # Same tender, buyer's own wording: shorter, different casing and punctuation.
        title="Evénementiel : location, montage et démontage de matériel pour l'alimentation électrique",
        # BOAMP publishes a day earlier than the JOUE mirror - the cluster must keep the earliest.
        published_at=datetime(2026, 8, 10, tzinfo=UTC),
        description="Marché de location de matériel d'éclairage pour manifestations.",
    )

    clusters = cluster_notices([ted, boamp])

    assert len(clusters) == 1, "the same tender from two sources must collapse to one card"
    cluster = clusters[0]
    assert {ref.source for ref in cluster.source_refs} == {"eu-ted", "fr-boamp"}
    assert cluster.canonical.dates.published_at == datetime(2026, 8, 10, tzinfo=UTC)
    assert cluster.canonical.description, "the richer description must survive the merge"
    assert cluster.merged_from[0]["reason"] == MatchReason.BUYER_DEADLINE_TITLE.value


def test_clustering_is_reentrant_for_late_arriving_duplicates():
    """A duplicate discovered days later must join the existing cluster, not create a second
    card (SPEC §6.7 "MUST be re-entrant")."""
    first = make_notice(source="eu-ted", external_id="1-2026", title="Maintenance multitechnique des lycées")
    cluster = cluster_notices([first])[0]

    late = make_notice(
        source="fr-maximilien", external_id="MX-42", title="Maintenance multitechnique des lycees"
    )
    verdict = same_tender(cluster.canonical, late)
    assert verdict.matched
    cluster.absorb(late, verdict)

    assert {ref.source for ref in cluster.source_refs} == {"eu-ted", "fr-maximilien"}
    assert len(cluster.merged_from) == 1


# ---------------------------------------------------------------- resolution keys


def test_explicit_cross_reference_wins_over_weak_similarity():
    """Key (1): a shared publication id is conclusive even when titles diverge."""
    left = make_notice(source="eu-ted", external_id="2026/S 155-553797", title="Travaux de voirie")
    right = CanonicalNotice(
        source_refs=[
            SourceRef(source="fr-boamp", external_id="26-79589"),
            SourceRef(source="fr-boamp", external_id="2026/S 155-553797"),
        ],
        notice_type=NoticeType.COMPETITION,
        country="FR",
        buyer=Buyer(name="Un autre acheteur"),
        title="Réfection des couches de roulement",
        dates=Dates(deadline_at=DEADLINE + timedelta(days=9)),
        provenance=Provenance(adapter="boamp@1.0.0", normalized_at=datetime.now(UTC)),
    )
    verdict = same_tender(left, right)
    assert verdict.matched
    assert verdict.reason is MatchReason.CROSS_REFERENCE
    assert verdict.confidence == 1.0


def test_buyer_siren_beats_name_spelling_differences():
    """Key (2): buyer identity is the SIREN when known, so spelling stops mattering."""
    left = make_notice(
        source="eu-ted",
        external_id="a",
        title="Nettoyage des locaux administratifs",
        buyer_name="CA2BM",
        buyer_siren="200069029",
    )
    right = make_notice(
        source="fr-boamp",
        external_id="b",
        title="Nettoyage des locaux administratifs et de la vitrerie",
        buyer_name="Communauté d'Agglomération des deux Baies en Montreuillois",
        buyer_siren="200069029",
    )
    assert same_tender(left, right).matched


def test_deadline_more_than_one_hour_apart_is_not_a_duplicate():
    """§6.7 allows ±1h. Two tenders from one buyer on the same day are common and distinct."""
    left = make_notice(source="eu-ted", external_id="a", title="Fourniture de repas en liaison froide")
    right = make_notice(
        source="fr-boamp",
        external_id="b",
        title="Fourniture de repas en liaison froide",
        deadline=DEADLINE + timedelta(hours=3),
    )
    verdict = same_tender(left, right)
    assert not verdict.matched


def test_refetching_the_same_publication_is_idempotent():
    """Ingestion replays and re-fetches must not produce a second card.

    Planning notices are the sharp case: with no deadline, key (2) cannot fire, so the
    identity check on source+external_id has to come first.
    """
    notice = make_notice(
        source="eu-ted",
        external_id="553797-2026",
        title="RFI Solution de gestion des subventions",
        deadline=None,
        notice_type=NoticeType.PLANNING,
    )
    again = notice.model_copy(deep=True)

    verdict = same_tender(notice, again)
    assert verdict.matched
    assert verdict.details["shared_reference"] == "same_publication"

    clusters = cluster_notices([notice, again])
    assert len(clusters) == 1
    assert len(clusters[0].source_refs) == 1, "the duplicate reference must not be added twice"


def test_short_external_ids_are_not_treated_as_cross_references():
    """A source-local id like "42" could collide across portals; only long references count."""
    left = make_notice(source="eu-ted", external_id="42", title="Travaux de voirie", buyer_name="Acheteur A")
    right = make_notice(
        source="fr-boamp", external_id="42", title="Fourniture de repas", buyer_name="Acheteur B"
    )
    assert not same_tender(left, right).matched


def test_missing_deadlines_do_not_collapse_unrelated_notices():
    """Planning notices rarely carry a deadline; two None values must not compare equal."""
    left = make_notice(
        source="eu-ted",
        external_id="a",
        title="RFI solution de gestion",
        deadline=None,
        notice_type=NoticeType.PLANNING,
    )
    right = make_notice(
        source="eu-ted",
        external_id="b",
        title="RFI solution de gestion",
        deadline=None,
        notice_type=NoticeType.PLANNING,
    )
    assert not same_tender(left, right).matched
    assert len(cluster_notices([left, right])) == 2


def test_different_notice_types_never_merge():
    """A competition notice and its award notice describe one procurement through two
    publications. Merging them would hide either the deadline or the winner."""
    competition = make_notice(
        source="eu-ted", external_id="a", title="Maintenance des installations de froid"
    )
    award = make_notice(
        source="eu-ted",
        external_id="b",
        title="Maintenance des installations de froid",
        notice_type=NoticeType.RESULT,
    )
    verdict = same_tender(competition, award)
    assert not verdict.matched
    assert verdict.details["blocked_by"] == "notice_type"


def test_embedding_fallback_matches_only_within_country_and_24h():
    """Key (3): cosine >= 0.92, same country, deadlines within 24h."""
    left = make_notice(
        source="eu-ted", external_id="a", title="Prestations de nettoyage", buyer_name="Acheteur A"
    )
    right = make_notice(
        source="fr-boamp",
        external_id="b",
        title="Services de propreté",
        buyer_name="Acheteur B",
        deadline=DEADLINE + timedelta(hours=6),
    )
    near = [1.0, 0.0, 0.0]
    also_near = [0.97, 0.24, 0.0]  # cosine ~= 0.97
    assert same_tender(left, right, left_embedding=near, right_embedding=also_near).matched

    far = [0.0, 1.0, 0.0]
    assert not same_tender(left, right, left_embedding=near, right_embedding=far).matched

    # Same vectors, different country: still not a duplicate.
    belgian = make_notice(
        source="be-bosa", external_id="c", title="Services de propreté", country="BE", buyer_name="Acheteur B"
    )
    assert not same_tender(left, belgian, left_embedding=near, right_embedding=also_near).matched


# ---------------------------------------------------------------- text normalisation


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("Commune d'Hyères", "COMMUNE DE HYERES", True),
        ("Département de la Vendée", "Departement de la Vendee", True),
        ("Commune de Lyon", "Commune de Paris", False),
    ],
)
def test_buyer_name_normalisation(left: str, right: str, expected: bool):
    same = normalize_buyer_name(left) == normalize_buyer_name(right)
    assert same is expected


def test_buyer_noise_stripping_keeps_the_distinguishing_core():
    assert normalize_buyer_name("Commune de Nogent-sur-Oise") == "nogent sur oise"
    assert normalize_buyer_name("Dijon Métropole") == "dijon"
    # When the boilerplate is the entire name there is nothing left to strip.
    assert normalize_buyer_name("Métropole") == "metropole"
    assert normalize_buyer_name(None) == ""


def test_trigram_similarity_bounds():
    assert trigram_similarity("maintenance des lycées", "maintenance des lycees") > 0.6
    assert trigram_similarity("fourniture de repas", "travaux de voirie") < 0.4
    assert trigram_similarity("", "anything") == 0.0
    assert trigram_similarity("identical", "identical") == 1.0


# ---------------------------------------------------------------- merge policy


def test_merge_follows_the_spec_policy():
    """Richest description wins, earliest publication date, union of documents and URLs."""
    canonical = make_notice(
        source="eu-ted",
        external_id="a",
        title="Court titre",
        description="Courte description.",
        published_at=datetime(2026, 8, 11, tzinfo=UTC),
        cpv=["31600000"],
        nuts=["FRL05"],
        documents=[DocumentRef(kind=DocumentKind.RC, title="RC.pdf", url="https://ted.test/rc")],
    )
    incoming = make_notice(
        source="fr-boamp",
        external_id="b",
        title="Un titre nettement plus descriptif du besoin réel",
        description="Une description beaucoup plus riche, avec le détail des prestations attendues.",
        published_at=datetime(2026, 8, 9, tzinfo=UTC),
        cpv=["51110000"],
        nuts=["FRL0"],
        amount=450000.0,
        documents=[DocumentRef(kind=DocumentKind.CCTP, title="CCTP.pdf", url="https://boamp.test/cctp")],
    )
    incoming.buyer.siren = "213000123"

    merged = merge(canonical, incoming)

    assert merged.title == incoming.title
    assert merged.description == incoming.description
    assert merged.dates.published_at == datetime(2026, 8, 9, tzinfo=UTC)
    assert merged.cpv == ["31600000", "51110000"]
    assert merged.nuts == ["FRL05", "FRL0"]
    assert merged.amounts.estimated_total == 450000.0
    assert merged.buyer.siren == "213000123", "a resolved SIREN completes the canonical record"
    assert {doc.url for doc in merged.documents} == {"https://ted.test/rc", "https://boamp.test/cctp"}
    assert len(merged.source_refs) == 2


def test_merge_does_not_overwrite_a_known_deadline():
    """Sources sometimes disagree on the deadline. The first value stands and the conflict
    stays visible per source: silently adopting the later date could cost a submission."""
    canonical = make_notice(source="eu-ted", external_id="a", title="Titre", deadline=DEADLINE)
    incoming = make_notice(
        source="fr-boamp", external_id="b", title="Titre", deadline=DEADLINE + timedelta(days=2)
    )

    merged = merge(canonical, incoming)
    assert merged.dates.deadline_at == DEADLINE


def test_merge_fills_a_missing_deadline():
    canonical = make_notice(source="eu-ted", external_id="a", title="Titre", deadline=None)
    incoming = make_notice(source="fr-boamp", external_id="b", title="Titre", deadline=DEADLINE)
    assert merge(canonical, incoming).dates.deadline_at == DEADLINE


def test_merge_is_idempotent_for_an_already_absorbed_source():
    """Re-processing the same payload must not duplicate its source reference."""
    canonical = make_notice(source="eu-ted", external_id="a", title="Titre")
    incoming = make_notice(source="fr-boamp", external_id="b", title="Titre")
    once = merge(canonical, incoming)
    twice = merge(once, incoming)
    assert len(twice.source_refs) == 2


def test_three_sources_collapse_to_one_cluster():
    """The realistic case: TED + BOAMP + a Tier-2 platform (SPEC §6.7)."""
    title = "Maintenance multitechnique des lycées - 4 lots"
    notices = [
        make_notice(source="eu-ted", external_id="1", title=title),
        make_notice(source="fr-boamp", external_id="2", title=title.lower()),
        make_notice(
            source="fr-maximilien", external_id="3", title="Maintenance multitechnique des lycees, 4 lots"
        ),
    ]
    clusters = cluster_notices(notices)
    assert len(clusters) == 1
    assert len(clusters[0].source_refs) == 3
    assert clusters[0].cluster_id == "cls_000001"
