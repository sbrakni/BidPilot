"""Matching & scoring engine (SPEC F3, §8).

Rank the daily stream against each org's profile so the inbox holds only plausible
tenders, each with an explainable score.

Two rules shape the whole module:

  * **Cost control (§8.1).** A three-stage funnel: free SQL-shaped hard filters, then a
    cheap semantic/keyword score, then a metered LLM verdict for the top candidates only.
    Stage 3 must never run on a notice that failed stage 1 - asserted in tests, because
    the failure mode is a silent cloud bill, not an error.
  * **No opaque numbers (P4).** `score_notice` returns the factor breakdown alongside the
    score, and the factors sum exactly to the score. The UI renders that breakdown; a
    number a user cannot interrogate is a number they will not trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from .canonical import CanonicalNotice, NoticeType
from .dedupe import cosine_similarity, normalize_text

# ---------------------------------------------------------------- profile & filters


@dataclass
class WatchFilters:
    """Stage-1 hard filters (SPEC §8.1). Every field is a veto, not a preference."""

    cpv_families: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    nuts: list[str] = field(default_factory=list)
    min_days_to_deadline: int | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    procedure_types: list[str] = field(default_factory=list)
    notice_types: list[NoticeType] = field(default_factory=lambda: [NoticeType.COMPETITION])
    keywords_include: list[str] = field(default_factory=list)
    keywords_exclude: list[str] = field(default_factory=list)


@dataclass
class CompanyProfile:
    """The subset of F2 (§7) that matching consumes."""

    org_id: str
    country: str = "FR"
    cpv_families: list[str] = field(default_factory=list)
    zones_nuts: list[str] = field(default_factory=list)
    national: bool = False
    keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    annual_revenue: float | None = None
    certifications: list[str] = field(default_factory=list)
    capability_embedding: list[float] | None = None
    known_buyer_sirens: list[str] = field(default_factory=list)


class FilterOutcome(StrEnum):
    PASS = "pass"
    CPV = "cpv_mismatch"
    COUNTRY = "country_excluded"
    GEOGRAPHY = "geography_excluded"
    DEADLINE = "deadline_too_close"
    AMOUNT = "amount_out_of_range"
    PROCEDURE = "procedure_excluded"
    NOTICE_TYPE = "notice_type_excluded"
    KEYWORD_MISSING = "required_keyword_missing"
    KEYWORD_EXCLUDED = "excluded_keyword_present"


def cpv_matches(code: str, families: list[str]) -> bool:
    """CPV is hierarchical: `72` matches `72000000`, `7221` matches `72212000`.

    Families are prefixes, so a user asking for "IT services" (72) gets the whole subtree
    without enumerating hundreds of codes.
    """
    return any(code.startswith(family.rstrip("*")) for family in families if family)


def nuts_matches(code: str, zones: list[str]) -> bool:
    """NUTS is hierarchical too, and a notice may be coded at any level.

    Bidirectional prefix matching: a `FR10`-scoped org matches a `FR101` notice (the
    tender sits inside their zone) and a `FR1`-coded notice (their zone sits inside the
    tender's area). Either way the work is reachable.
    """
    for zone in zones:
        if not zone:
            continue
        if code.startswith(zone) or zone.startswith(code):
            return True
    return False


def apply_hard_filters(
    notice: CanonicalNotice,
    filters: WatchFilters,
    *,
    now: datetime | None = None,
) -> FilterOutcome:
    """Stage 1: free, deterministic vetoes. Returns the first failing reason."""
    now = now or datetime.now(UTC)

    if filters.notice_types and notice.notice_type not in filters.notice_types:
        return FilterOutcome.NOTICE_TYPE

    if filters.countries and notice.country not in filters.countries:
        return FilterOutcome.COUNTRY

    if filters.cpv_families:
        codes = [*notice.cpv, *(c for lot in notice.lots for c in lot.cpv)]
        if not any(cpv_matches(code, filters.cpv_families) for code in codes):
            return FilterOutcome.CPV

    if filters.nuts:
        places = [*notice.nuts, *(p for lot in notice.lots for p in lot.place_nuts)]
        # A notice with no place coded cannot be excluded on geography - absent is not a
        # mismatch, and dropping it would hide tenders that are merely poorly tagged.
        if places and not any(nuts_matches(place, filters.nuts) for place in places):
            return FilterOutcome.GEOGRAPHY

    if (
        filters.min_days_to_deadline is not None
        and notice.dates.deadline_at is not None
        and notice.dates.deadline_at - now < timedelta(days=filters.min_days_to_deadline)
    ):
        return FilterOutcome.DEADLINE

    amount = notice.amounts.estimated_total
    if amount is not None:
        if filters.amount_min is not None and amount < filters.amount_min:
            return FilterOutcome.AMOUNT
        if filters.amount_max is not None and amount > filters.amount_max:
            return FilterOutcome.AMOUNT

    if filters.procedure_types:
        procedure = notice.procedure.type
        if procedure is None or str(procedure) not in filters.procedure_types:
            return FilterOutcome.PROCEDURE

    haystack = normalize_text(f"{notice.title} {notice.description or ''}")
    if filters.keywords_exclude and any(
        normalize_text(word) in haystack for word in filters.keywords_exclude if word
    ):
        return FilterOutcome.KEYWORD_EXCLUDED
    if filters.keywords_include and not any(
        normalize_text(word) in haystack for word in filters.keywords_include if word
    ):
        return FilterOutcome.KEYWORD_MISSING

    return FilterOutcome.PASS


# ---------------------------------------------------------------- stage 2: score

#: Default factor weights (SPEC §8.2). Config, overridable per org later (P4).
DEFAULT_WEIGHTS: dict[str, float] = {
    "activity_fit": 0.35,
    "geographic_fit": 0.20,
    "size_fit": 0.15,
    "certification_fit": 0.10,
    "buyer_familiarity": 0.05,
    "deadline_comfort": 0.15,
}

#: A tender worth more than this share of annual revenue is a size risk (SPEC §8.2).
REVENUE_RATIO_WARNING = 0.5

#: Comfortable preparation runway. Below it, deadline comfort decays toward zero.
COMFORTABLE_RUNWAY_DAYS = 30


@dataclass(frozen=True)
class ScoreFactor:
    key: str
    label_fr: str
    value: float  # 0..1, the raw fit
    weight: float
    points: float  # value * weight * 100 - what the UI renders in the breakdown bar
    detail: str = ""


@dataclass(frozen=True)
class MatchScore:
    score: int
    factors: list[ScoreFactor]
    warnings: list[str] = field(default_factory=list)

    @property
    def breakdown(self) -> dict[str, float]:
        return {factor.key: factor.points for factor in self.factors}

    def as_dict(self) -> dict[str, object]:
        """Shape persisted in `matches.breakdown` and rendered by the UI (SPEC §8.4)."""
        return {
            "score": self.score,
            "factors": [
                {
                    "key": f.key,
                    "label_fr": f.label_fr,
                    "value": round(f.value, 4),
                    "weight": f.weight,
                    "points": round(f.points, 2),
                    "detail": f.detail,
                }
                for f in self.factors
            ],
            "warnings": list(self.warnings),
        }


def _activity_fit(notice: CanonicalNotice, profile: CompanyProfile) -> tuple[float, str]:
    """Semantic similarity when an embedding exists, CPV/keyword overlap otherwise.

    Cold-start matters here: a brand-new org has no capability embedding yet (§23
    cold-start risk), and matching still has to work on day one.
    """
    if profile.capability_embedding:
        # Cosine runs [-1, 1]; rescale to [0, 1] so a negative similarity cannot
        # subtract points from other factors.
        cosine = cosine_similarity(profile.capability_embedding, _notice_embedding(notice))
        if cosine:
            return (cosine + 1) / 2, f"similarité sémantique {cosine:.2f}"

    codes = [*notice.cpv, *(c for lot in notice.lots for c in lot.cpv)]
    cpv_hits = [code for code in codes if cpv_matches(code, profile.cpv_families)]
    haystack = normalize_text(f"{notice.title} {notice.description or ''}")
    keyword_hits = [word for word in profile.keywords if word and normalize_text(word) in haystack]

    if not codes and not profile.keywords:
        return 0.0, "aucun code CPV publié"

    cpv_ratio = len(cpv_hits) / len(codes) if codes else 0.0
    keyword_ratio = min(1.0, len(keyword_hits) / 3) if profile.keywords else 0.0
    value = max(cpv_ratio, keyword_ratio)
    details = []
    if cpv_hits:
        details.append(f"CPV {', '.join(sorted(set(cpv_hits))[:3])}")
    if keyword_hits:
        details.append(f"mots-clés {', '.join(keyword_hits[:3])}")
    return value, " · ".join(details) or "aucune correspondance d'activité"


def _notice_embedding(notice: CanonicalNotice) -> list[float] | None:
    """Hook for the enrichment-stage embedding (§6.8), carried out of band.

    Kept as a seam so scoring stays a pure function and remains unit-testable without a
    model call.
    """
    return getattr(notice, "_embedding", None)


def _geographic_fit(notice: CanonicalNotice, profile: CompanyProfile) -> tuple[float, str]:
    if profile.national:
        return 1.0, "couverture nationale"
    places = [*notice.nuts, *(p for lot in notice.lots for p in lot.place_nuts)]
    if not places:
        # Unknown place: neutral, never a penalty (P1 - we do not invent a location).
        return 0.5, "lieu d'exécution non publié"
    if not profile.zones_nuts:
        return 0.5, "zones d'intervention non renseignées"
    hits = [place for place in places if nuts_matches(place, profile.zones_nuts)]
    if not hits:
        return 0.0, f"hors zones ({', '.join(sorted(set(places))[:3])})"
    # Depth of the match: an exact regional hit beats a country-level one.
    best = max(len(place) for place in hits)
    return min(1.0, 0.7 + 0.1 * (best - 2)), f"zone couverte ({', '.join(sorted(set(hits))[:2])})"


def _size_fit(notice: CanonicalNotice, profile: CompanyProfile) -> tuple[float, str, str | None]:
    amount = notice.amounts.estimated_total
    if amount is None:
        return 0.5, "montant non publié", None
    if not profile.annual_revenue:
        return 0.5, "chiffre d'affaires non renseigné", None
    ratio = amount / profile.annual_revenue
    if ratio > REVENUE_RATIO_WARNING:
        # Deliberately not a veto: a big contract can be the right stretch, but the
        # brief must say so out loud (SPEC §8.2).
        return (
            max(0.0, 1.0 - ratio),
            f"{ratio:.0%} du CA annuel",
            f"Montant estimé = {ratio:.0%} du CA annuel (> {REVENUE_RATIO_WARNING:.0%})",
        )
    # The sweet spot is a contract that is material but not existential.
    return min(1.0, 0.5 + ratio), f"{ratio:.0%} du CA annuel", None


def _certification_fit(notice: CanonicalNotice, profile: CompanyProfile) -> tuple[float, str]:
    """Pre-flag certifications named in the notice text (SPEC §20.2 "Qualiopi requise").

    A notice-level heuristic only. The authoritative check reads the RC and is part of
    eligibility (§10.1); this exists so the inbox card can warn before anything is read.
    """
    haystack = normalize_text(f"{notice.title} {notice.description or ''}")
    demanded = [name for name in KNOWN_CERTIFICATIONS if normalize_text(name) in haystack]
    if not demanded:
        return 1.0, "aucune certification détectée dans l'avis"
    held = {normalize_text(c) for c in profile.certifications}
    missing = [name for name in demanded if normalize_text(name) not in held]
    if not missing:
        return 1.0, f"certifications détenues ({', '.join(demanded)})"
    return 0.0, f"certification manquante ({', '.join(missing)})"


#: Certifications common enough in FR/BE/LU notices to be worth a text pre-flag.
KNOWN_CERTIFICATIONS = (
    "Qualiopi",
    "ISO 9001",
    "ISO 14001",
    "ISO 27001",
    "MASE",
    "Qualibat",
    "Qualifelec",
    "RGE",
    "HDS",
    "SecNumCloud",
)


def _buyer_familiarity(notice: CanonicalNotice, profile: CompanyProfile) -> tuple[float, str]:
    siren = notice.buyer.siren_effective
    if siren and siren in profile.known_buyer_sirens:
        return 1.0, "acheteur déjà rencontré"
    return 0.0, "nouvel acheteur"


def _deadline_comfort(notice: CanonicalNotice, now: datetime) -> tuple[float, str]:
    deadline = notice.dates.deadline_at
    if deadline is None:
        return 0.5, "date limite non publiée"
    days = (deadline - now).total_seconds() / 86400
    if days <= 0:
        return 0.0, "date limite dépassée"
    return min(1.0, days / COMFORTABLE_RUNWAY_DAYS), f"{days:.0f} jours restants"


def score_notice(
    notice: CanonicalNotice,
    profile: CompanyProfile,
    *,
    weights: dict[str, float] | None = None,
    now: datetime | None = None,
) -> MatchScore:
    """Stage 2: the explainable 0-100 match score (SPEC §8.2).

    `score = 100 x sum(wi . fi)`, and the returned factor points sum to exactly the score,
    which §8.4 requires the UI to be able to show.
    """
    weights = weights or DEFAULT_WEIGHTS
    now = now or datetime.now(UTC)
    warnings: list[str] = []

    activity_value, activity_detail = _activity_fit(notice, profile)
    geo_value, geo_detail = _geographic_fit(notice, profile)
    size_value, size_detail, size_warning = _size_fit(notice, profile)
    cert_value, cert_detail = _certification_fit(notice, profile)
    buyer_value, buyer_detail = _buyer_familiarity(notice, profile)
    deadline_value, deadline_detail = _deadline_comfort(notice, now)
    if size_warning:
        warnings.append(size_warning)
    if cert_value == 0.0:
        # Uppercase the first letter only: `str.capitalize()` would lowercase the rest and
        # turn "Qualiopi, ISO 27001" into "qualiopi, iso 27001" in a user-facing warning.
        warnings.append(cert_detail[0].upper() + cert_detail[1:] if cert_detail else "")

    specs = (
        ("activity_fit", "Activité", activity_value, activity_detail),
        ("geographic_fit", "Géographie", geo_value, geo_detail),
        ("size_fit", "Taille", size_value, size_detail),
        ("certification_fit", "Certifications", cert_value, cert_detail),
        ("buyer_familiarity", "Acheteur", buyer_value, buyer_detail),
        ("deadline_comfort", "Délai", deadline_value, deadline_detail),
    )

    factors: list[ScoreFactor] = []
    for key, label, value, detail in specs:
        weight = weights.get(key, 0.0)
        clamped = max(0.0, min(1.0, value))
        factors.append(
            ScoreFactor(
                key=key,
                label_fr=label,
                value=clamped,
                weight=weight,
                points=clamped * weight * 100,
                detail=detail,
            )
        )

    # Round once, at the end, so the rendered factors always sum to the rendered score.
    total = sum(factor.points for factor in factors)
    return MatchScore(score=round(total), factors=factors, warnings=warnings)


# ---------------------------------------------------------------- funnel


#: Stage-3 LLM qualification only above this score, or on explicit user open (SPEC §8.1).
LLM_QUALIFICATION_THRESHOLD = 55

#: Instant push instead of the daily digest above this score (SPEC §8.3).
INSTANT_ALERT_THRESHOLD = 80


@dataclass(frozen=True)
class FunnelResult:
    notice_external_id: str
    outcome: FilterOutcome
    score: MatchScore | None = None
    llm_eligible: bool = False

    @property
    def passed(self) -> bool:
        return self.outcome is FilterOutcome.PASS


def run_funnel(
    notices: list[CanonicalNotice],
    profile: CompanyProfile,
    filters: WatchFilters,
    *,
    weights: dict[str, float] | None = None,
    now: datetime | None = None,
    llm_threshold: int = LLM_QUALIFICATION_THRESHOLD,
) -> list[FunnelResult]:
    """Stages 1-2 for a batch, flagging which survivors deserve stage 3.

    `llm_eligible` is False for anything that failed stage 1, by construction: the cost
    guard is the control flow, not a check someone has to remember to write.
    """
    results: list[FunnelResult] = []
    for notice in notices:
        external_id = notice.source_refs[0].external_id
        outcome = apply_hard_filters(notice, filters, now=now)
        if outcome is not FilterOutcome.PASS:
            results.append(FunnelResult(external_id, outcome))
            continue
        score = score_notice(notice, profile, weights=weights, now=now)
        results.append(
            FunnelResult(
                notice_external_id=external_id,
                outcome=outcome,
                score=score,
                llm_eligible=score.score >= llm_threshold,
            )
        )
    return results
