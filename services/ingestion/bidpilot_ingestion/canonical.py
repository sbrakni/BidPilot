"""The canonical notice model - runtime contract for every adapter (SPEC Annex B.1).

The JSON Schema at packages/shared/schemas/notice.v1.json is the cross-language source
of truth; tests/test_schema_conformance.py asserts this model stays aligned with it.

Rule that governs every field here (SPEC §6.2): *absent means absent*. An adapter that
cannot find a value leaves it None. It never guesses, never defaults, never back-fills -
because a fabricated deadline or a fabricated CPV silently poisons matching and, worse,
the Go/No-Go verdict.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1


class _Model(BaseModel):
    """Strict base: an unexpected key is a source that changed shape, so we want to know."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoticeType(StrEnum):
    PLANNING = "planning"
    COMPETITION = "competition"
    RESULT = "result"


class NoticeStatus(StrEnum):
    ACTIVE = "active"
    AMENDED = "amended"
    CLOSED = "closed"
    AWARDED = "awarded"
    CANCELLED = "cancelled"


class ProcedureType(StrEnum):
    OPEN = "open"
    RESTRICTED = "restricted"
    NEGOTIATED = "negotiated"
    COMPETITIVE_DIALOGUE = "competitive-dialogue"
    INNOVATION_PARTNERSHIP = "innovation-partnership"
    DIRECT_AWARD = "direct-award"
    ADAPTED = "adapted"
    OTHER = "other"


class ContractNature(StrEnum):
    WORKS = "works"
    SUPPLIES = "supplies"
    SERVICES = "services"


class DocumentKind(StrEnum):
    """DCE document families (SPEC §5). Classification of an unknown file is `other`."""

    RC = "rc"
    CCAP = "ccap"
    CCTP = "cctp"
    AE = "ae"
    PRICE = "price"
    DUME = "dume"
    ANNEX = "annex"
    AMENDMENT = "amendment"
    OTHER = "other"


class SourceRef(_Model):
    source: str
    external_id: str
    url: str | None = None
    fetched_at: datetime | None = None


class Contact(_Model):
    email: str | None = None
    phone: str | None = None
    website: str | None = None


class Address(_Model):
    street: str | None = None
    postcode: str | None = None
    city: str | None = None
    nuts: str | None = None
    country: str | None = None


class Buyer(_Model):
    name: str | None = None
    siren: str | None = None
    siret: str | None = None
    national_id: str | None = None
    contact: Contact = Field(default_factory=Contact)
    address: Address = Field(default_factory=Address)

    @field_validator("siren", "siret", mode="before")
    @classmethod
    def _digits_only(cls, v: Any) -> Any:
        """SIREN/SIRET arrive spaced or dotted from several sources; keep digits only."""
        if v is None:
            return None
        digits = "".join(ch for ch in str(v) if ch.isdigit())
        return digits or None

    @field_validator("siren")
    @classmethod
    def _siren_len(cls, v: str | None) -> str | None:
        return v if v is not None and len(v) == 9 else None

    @field_validator("siret")
    @classmethod
    def _siret_len(cls, v: str | None) -> str | None:
        return v if v is not None and len(v) == 14 else None

    @property
    def siren_effective(self) -> str | None:
        """SIREN is the first 9 digits of a SIRET - the identity used for dedupe (§6.7)."""
        return self.siren or (self.siret[:9] if self.siret else None)


class Procedure(_Model):
    type: ProcedureType | None = None
    national_label: str | None = None
    framework: bool | None = None
    reserved_sme: bool | None = None
    contract_nature: list[ContractNature] = Field(default_factory=list)


class Lot(_Model):
    lot_id: str
    title: str | None = None
    description: str | None = None
    cpv: list[str] = Field(default_factory=list)
    amount_est: float | None = None
    currency: str | None = None
    place_nuts: list[str] = Field(default_factory=list)
    deadline_at: datetime | None = None


class Amounts(_Model):
    estimated_total: float | None = None
    currency: str | None = None
    vat: str | None = None  # "HT" | "TTC" | None - unknown stays unknown


class Visit(_Model):
    """A mandatory site visit is frequently eliminatory, hence first-class (SPEC §10.1)."""

    mandatory: bool | None = None
    dates: list[str] = Field(default_factory=list)


class Dates(_Model):
    published_at: datetime | None = None
    deadline_at: datetime | None = None
    questions_deadline_at: datetime | None = None
    visit: Visit = Field(default_factory=Visit)
    tz: str | None = None


class DocumentRef(_Model):
    kind: DocumentKind = DocumentKind.OTHER
    title: str | None = None
    url: str | None = None
    file_key: str | None = None
    pages: int | None = None


class Urls(_Model):
    notice: str | None = None
    documents: str | None = None
    submission: str | None = None


class Award(_Model):
    supplier_name: str | None = None
    supplier_siren: str | None = None
    amount: float | None = None
    currency: str | None = None
    signed_at: str | None = None
    duration_months: int | None = None


class Provenance(_Model):
    adapter: str
    normalized_at: datetime
    raw_content_hash: str | None = None
    unmapped_fields: list[str] = Field(default_factory=list)


class CanonicalNotice(_Model):
    schema_version: int = SCHEMA_VERSION
    uid: str | None = None
    cluster_id: str | None = None
    source_refs: list[SourceRef] = Field(min_length=1)
    notice_type: NoticeType
    status: NoticeStatus = NoticeStatus.ACTIVE
    country: str
    language: str | None = None
    buyer: Buyer = Field(default_factory=Buyer)
    title: str = Field(min_length=1)
    description: str | None = None
    cpv: list[str] = Field(default_factory=list)
    nuts: list[str] = Field(default_factory=list)
    procedure: Procedure = Field(default_factory=Procedure)
    lots: list[Lot] = Field(default_factory=list)
    amounts: Amounts = Field(default_factory=Amounts)
    dates: Dates = Field(default_factory=Dates)
    documents: list[DocumentRef] = Field(default_factory=list)
    urls: Urls = Field(default_factory=Urls)
    requires_account_for_docs: bool | None = None
    award: Award | None = None
    provenance: Provenance
    version: int = 1

    @field_validator("country")
    @classmethod
    def _country_alpha2(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 2 or not v.isalpha():
            raise ValueError(f"country must be ISO-3166 alpha-2, got {v!r}")
        return v

    @field_validator("cpv")
    @classmethod
    def _clean_cpv(cls, v: list[str]) -> list[str]:
        """Sources emit CPV duplicated, spaced, or with a check digit suffix (`72000000-5`)."""
        out: list[str] = []
        for code in v:
            digits = "".join(ch for ch in str(code) if ch.isdigit())[:8]
            if len(digits) == 8 and digits not in out:
                out.append(digits)
        return out

    @field_validator("nuts")
    @classmethod
    def _clean_nuts(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for code in v:
            c = str(code).strip().upper()
            # TED mixes NUTS with ISO-3166 alpha-3 country codes in the same array;
            # a 3-letter all-alpha token is a country, not a region.
            if len(c) == 3 and c.isalpha():
                continue
            if 2 <= len(c) <= 5 and c[:2].isalpha() and c not in out:
                out.append(c)
        return out


class RawNotice(_Model):
    """What `fetch_since` yields: the untouched payload plus enough to re-fetch it.

    Raw payloads are kept forever (SPEC §6.2) so parsers can be improved and replayed.
    """

    source: str
    external_id: str
    payload: dict[str, Any]
    url: str | None = None
    fetched_at: datetime | None = None
    content_hash: str | None = None
