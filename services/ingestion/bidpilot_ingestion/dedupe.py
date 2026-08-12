"""Deduplication & clustering (SPEC §6.7).

The same tender legitimately appears on TED *and* BOAMP *and* a buyer platform. Users
must see **one** card carrying every source badge. This module decides whether two
canonical notices are the same tender, and how to merge them.

Resolution keys, applied in the spec's order of authority:
  1. explicit cross-references between publications (a national id quoted by TED, or
     vice versa) - conclusive when present;
  2. buyer identity + submission deadline (±1h) + title trigram similarity >= 0.6;
  3. embedding similarity >= 0.92 + same country + deadline within 24h.

Two properties matter as much as accuracy:
  * **re-entrant** - a duplicate arriving days later merges into the existing cluster;
  * **reversible** - a bad merge can be split, and the decision is logged as training data.
Both follow from clustering being a pure function of (candidate, existing cluster) with an
explicit, inspectable reason on every match.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from .canonical import CanonicalNotice, SourceRef

# Thresholds from SPEC §6.7. Config, not magic numbers: tuning these is a product decision.
TITLE_SIMILARITY_THRESHOLD = 0.6
EMBEDDING_SIMILARITY_THRESHOLD = 0.92
DEADLINE_TOLERANCE = timedelta(hours=1)
EMBEDDING_DEADLINE_TOLERANCE = timedelta(hours=24)

#: Minimum length for an external id to be trusted as a cross-source reference.
#: Real publication references are long ("2026/S 155-553797", "26-79589"); a short id is
#: source-local and could collide across portals by pure chance.
MIN_CROSS_REFERENCE_LEN = 6

# Legal forms and procurement boilerplate carry no distinguishing signal, and their
# presence inflates trigram overlap between unrelated notices from the same buyer type.
_BUYER_NOISE = (
    "commune de",
    "commune d",
    "ville de",
    "ville d",
    "departement de",
    "departement du",
    "departement d",
    "conseil departemental",
    "conseil regional",
    "region",
    "communaute d agglomeration",
    "communaute de communes",
    "communaute urbaine",
    "metropole",
    "syndicat",
    "etablissement public",
    "centre hospitalier",
    "chu",
    "ch",
    "mairie de",
    "mairie d",
    "sa",
    "sas",
    "sarl",
    "sasu",
    "eurl",
    "spa",
    "nv",
    "bv",
    "cvba",
    "asbl",
    "vzw",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class MatchReason(StrEnum):
    """Why two notices were considered the same. Displayed in ops UI, kept for audit."""

    CROSS_REFERENCE = "cross_reference"
    BUYER_DEADLINE_TITLE = "buyer_deadline_title"
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class MatchVerdict:
    matched: bool
    reason: MatchReason | None = None
    confidence: float = 0.0
    details: dict[str, float | str] = field(default_factory=dict)


def strip_accents(text: str) -> str:
    """`Métropole` and `Metropole` are the same buyer to a human, so also to us."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return _NON_ALNUM.sub(" ", strip_accents(text).lower()).strip()


def normalize_buyer_name(name: str | None) -> str:
    """Reduce a buyer name to its distinguishing core.

    "Commune d'Hyères" and "COMMUNE DE HYERES" must collide; "Commune de Lyon" and
    "Commune de Paris" must not - so only leading/trailing boilerplate is removed, never
    an interior word that might be the actual name.
    """
    text = normalize_text(name)
    if not text:
        return ""
    changed = True
    while changed:
        changed = False
        for noise in _BUYER_NOISE:
            if text == noise:
                return text  # the boilerplate *is* the whole name; keep it
            if text.startswith(f"{noise} "):
                text = text[len(noise) + 1 :]
                changed = True
            if text.endswith(f" {noise}"):
                text = text[: -len(noise) - 1]
                changed = True
    return text.strip()


def trigrams(text: str) -> set[str]:
    """Character trigrams of a normalised string, matching Postgres pg_trgm's approach.

    Keeping the same notion of similarity in Python and in SQL means the clustering
    decision made here can be reproduced by a query later.
    """
    cleaned = normalize_text(text)
    if not cleaned:
        return set()
    padded = f"  {cleaned} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def trigram_similarity(left: str | None, right: str | None) -> float:
    """Jaccard similarity over trigram sets, in [0, 1]."""
    a, b = trigrams(left or ""), trigrams(right or "")
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right, strict=True))
    norm_left = sum(x * x for x in left) ** 0.5
    norm_right = sum(y * y for y in right) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_left * norm_right)))


def buyer_key(notice: CanonicalNotice) -> str | None:
    """The strongest available buyer identity: SIREN if known, else normalised name."""
    siren = notice.buyer.siren_effective
    if siren:
        return f"siren:{siren}"
    name = normalize_buyer_name(notice.buyer.name)
    return f"name:{name}" if name else None


def external_ids(notice: CanonicalNotice) -> set[str]:
    return {f"{ref.source}:{ref.external_id}" for ref in notice.source_refs}


def cross_references(notice: CanonicalNotice) -> set[str]:
    """Publication ids this notice points at, in a comparable form.

    TED quotes the national publication id and BOAMP quotes the JOUE number, so a shared
    id is the cheapest and most reliable link we get. Bare `external_id`s are compared
    source-agnostically because the two sides label them differently.
    """
    return {
        ref.external_id.strip().upper()
        for ref in notice.source_refs
        if len(ref.external_id.strip()) >= MIN_CROSS_REFERENCE_LEN
    }


def same_tender(
    left: CanonicalNotice,
    right: CanonicalNotice,
    *,
    left_embedding: list[float] | None = None,
    right_embedding: list[float] | None = None,
) -> MatchVerdict:
    """Decide whether two canonical notices describe the same tender.

    Ordered by authority; the first key that fires wins, and reports why.
    """
    # A result notice and a competition notice are different publications about the same
    # procurement, not duplicates: merging them would hide the award or the deadline.
    if left.notice_type is not right.notice_type:
        return MatchVerdict(False, details={"blocked_by": "notice_type"})

    # (0) the very same publication, seen twice: a re-fetch or a pipeline replay.
    # Checked before anything else so ingestion is idempotent even for notices that carry
    # no deadline (planning notices), where key (2) cannot fire and the duplicate would
    # otherwise become a second card.
    if external_ids(left) & external_ids(right):
        return MatchVerdict(True, MatchReason.CROSS_REFERENCE, 1.0, {"shared_reference": "same_publication"})

    # (1) explicit cross-references between two different publications
    shared = cross_references(left) & cross_references(right)
    if shared:
        return MatchVerdict(
            True, MatchReason.CROSS_REFERENCE, 1.0, {"shared_reference": sorted(shared)[0]}
        )

    # (2) buyer identity + deadline (±1h) + title similarity
    left_key, right_key = buyer_key(left), buyer_key(right)
    left_deadline, right_deadline = left.dates.deadline_at, right.dates.deadline_at
    title_score = trigram_similarity(left.title, right.title)
    if (
        left_key is not None
        and left_key == right_key
        and left_deadline is not None
        and right_deadline is not None
        and abs(left_deadline - right_deadline) <= DEADLINE_TOLERANCE
        and title_score >= TITLE_SIMILARITY_THRESHOLD
    ):
        return MatchVerdict(
            True,
            MatchReason.BUYER_DEADLINE_TITLE,
            # Confidence tracks how far past the bar the weakest signal is.
            round(min(1.0, 0.6 + 0.4 * (title_score - TITLE_SIMILARITY_THRESHOLD) / 0.4), 3),
            {"title_similarity": round(title_score, 3), "buyer_key": left_key},
        )

    # (3) embedding fallback: same country, deadline within 24h
    if left.country == right.country and left_embedding and right_embedding:
        score = cosine_similarity(left_embedding, right_embedding)
        deadlines_close = (
            left_deadline is not None
            and right_deadline is not None
            and abs(left_deadline - right_deadline) <= EMBEDDING_DEADLINE_TOLERANCE
        )
        if score >= EMBEDDING_SIMILARITY_THRESHOLD and deadlines_close:
            return MatchVerdict(True, MatchReason.EMBEDDING, round(score, 3), {"cosine": round(score, 3)})
        return MatchVerdict(False, details={"cosine": round(score, 3)})

    return MatchVerdict(False, details={"title_similarity": round(title_score, 3)})


def merge(canonical: CanonicalNotice, incoming: CanonicalNotice) -> CanonicalNotice:
    """Field-level merge into the cluster's canonical notice (SPEC §6.7).

    Merge policy, straight from the spec: richest description wins, earliest publication
    date, union of documents/URLs - and every source reference preserved so the UI can
    say "Published on: BOAMP, TED, Maximilien".
    """
    merged = canonical.model_copy(deep=True)

    seen = external_ids(canonical)
    merged.source_refs = list(canonical.source_refs) + [
        ref for ref in incoming.source_refs if f"{ref.source}:{ref.external_id}" not in seen
    ]

    if len(incoming.title) > len(merged.title):
        merged.title = incoming.title
    if len(incoming.description or "") > len(merged.description or ""):
        merged.description = incoming.description

    merged.cpv = list(dict.fromkeys([*merged.cpv, *incoming.cpv]))
    merged.nuts = list(dict.fromkeys([*merged.nuts, *incoming.nuts]))

    # Earliest publication: the first time this tender became public anywhere.
    if incoming.dates.published_at and (
        merged.dates.published_at is None or incoming.dates.published_at < merged.dates.published_at
    ):
        merged.dates.published_at = incoming.dates.published_at
    # Deadlines and question deadlines: fill gaps only. Disagreement between sources is
    # not averaged away - the existing value stands and the conflict stays visible in the
    # per-source references, because a wrong deadline is the worst failure we can ship.
    if merged.dates.deadline_at is None:
        merged.dates.deadline_at = incoming.dates.deadline_at
    if merged.dates.questions_deadline_at is None:
        merged.dates.questions_deadline_at = incoming.dates.questions_deadline_at
    if merged.dates.tz is None:
        merged.dates.tz = incoming.dates.tz
    if merged.dates.visit.mandatory is None and incoming.dates.visit.mandatory is not None:
        merged.dates.visit = incoming.dates.visit

    # Buyer identity: a resolved SIREN/SIRET beats a name, so let one source complete another.
    if merged.buyer.siren is None and incoming.buyer.siren:
        merged.buyer.siren = incoming.buyer.siren
    if merged.buyer.siret is None and incoming.buyer.siret:
        merged.buyer.siret = incoming.buyer.siret
    if not merged.buyer.name and incoming.buyer.name:
        merged.buyer.name = incoming.buyer.name

    if merged.amounts.estimated_total is None:
        merged.amounts.estimated_total = incoming.amounts.estimated_total
        merged.amounts.currency = merged.amounts.currency or incoming.amounts.currency
    if merged.procedure.type is None:
        merged.procedure.type = incoming.procedure.type

    existing_urls = {doc.url for doc in merged.documents if doc.url}
    merged.documents = list(merged.documents) + [
        doc for doc in incoming.documents if doc.url and doc.url not in existing_urls
    ]
    merged.urls.notice = merged.urls.notice or incoming.urls.notice
    merged.urls.documents = merged.urls.documents or incoming.urls.documents
    merged.urls.submission = merged.urls.submission or incoming.urls.submission

    if not merged.lots and incoming.lots:
        merged.lots = list(incoming.lots)
    if merged.award is None and incoming.award is not None:
        merged.award = incoming.award

    return merged


@dataclass
class Cluster:
    """A tender seen through one or more publications."""

    cluster_id: str
    canonical: CanonicalNotice
    confidence: float = 1.0
    merged_from: list[dict[str, str]] = field(default_factory=list)

    @property
    def source_refs(self) -> list[SourceRef]:
        return self.canonical.source_refs

    def absorb(self, notice: CanonicalNotice, verdict: MatchVerdict) -> None:
        self.canonical = merge(self.canonical, notice)
        self.merged_from.append(
            {
                "source": notice.source_refs[0].source,
                "external_id": notice.source_refs[0].external_id,
                "reason": verdict.reason.value if verdict.reason else "unknown",
                "confidence": str(verdict.confidence),
            }
        )
        self.confidence = min(self.confidence, verdict.confidence or 1.0)


def cluster_notices(
    notices: list[CanonicalNotice],
    *,
    embeddings: dict[str, list[float]] | None = None,
    id_prefix: str = "cls",
) -> list[Cluster]:
    """Group a batch of canonical notices into clusters.

    Deliberately simple - a linear scan per candidate. At the design scale (~5k notices/day,
    SPEC §6.9) the real implementation narrows candidates with a SQL pre-filter on
    (country, deadline window, buyer key); this function is the decision logic that
    pre-filter feeds, and is what the tests pin down.
    """
    embeddings = embeddings or {}
    clusters: list[Cluster] = []
    for notice in notices:
        key = f"{notice.source_refs[0].source}:{notice.source_refs[0].external_id}"
        placed = False
        for cluster in clusters:
            canonical_key = (
                f"{cluster.canonical.source_refs[0].source}:{cluster.canonical.source_refs[0].external_id}"
            )
            verdict = same_tender(
                cluster.canonical,
                notice,
                left_embedding=embeddings.get(canonical_key),
                right_embedding=embeddings.get(key),
            )
            if verdict.matched:
                cluster.absorb(notice, verdict)
                placed = True
                break
        if not placed:
            clusters.append(
                Cluster(cluster_id=f"{id_prefix}_{len(clusters) + 1:06d}", canonical=notice.model_copy(deep=True))
            )
    return clusters
