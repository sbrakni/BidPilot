"""TED (EU) adapter - the all-EU backbone (SPEC §6.3).

Facts verified against the live API (2026-08), not assumed:
  * `POST https://api.ted.europa.eu/v3/notices/search` needs **no API key**.
  * There is **no `sort` parameter** - sending one is a 400. So incremental fetch cannot
    "read the newest first"; it must bound a publication-date window in the query itself
    and page through the whole window. That is why the cursor is a date watermark.
  * `fields` is validated server-side against ~1830 business terms; an unknown name fails
    the whole request. TED_FIELDS below is the verified subset.
  * Multilingual values arrive as `{"fra": "..."}` or `{"fra": ["..."]}` keyed by ISO-639-3.
  * `place-of-performance` mixes NUTS codes with ISO-3166 alpha-3 country codes, and
    repeats a value per lot. `classification-cpv` and `contract-nature` repeat likewise.
  * `publication-date` is a *date with offset* (`2026-08-11+02:00`), not a timestamp.
  * A lot deadline arrives split across `deadline-receipt-tender-date-lot` and
    `...-time-lot`, both offset-bearing; `deadline-receipt-request` carries a full instant.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from ..canonical import (
    Amounts,
    Buyer,
    CanonicalNotice,
    Dates,
    Lot,
    NoticeType,
    Procedure,
    Provenance,
    RawNotice,
    SourceRef,
    Urls,
)
from ..eforms import ISO3_TO_COUNTRY2, country2, lang2, pick_lang
from .base import BaseAdapter, Cursor, register

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"

# Verified against the live field catalogue. Adding a name here without checking it
# against the API breaks every request, so keep it in sync with scripts/capture_fixtures.py.
TED_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "organisation-name-buyer",
    "organisation-country-buyer",
    "publication-date",
    "deadline-receipt-request",
    "deadline-receipt-tender-date-lot",
    "deadline-receipt-tender-time-lot",
    "classification-cpv",
    "place-of-performance",
    "notice-type",
    "procedure-type",
    "contract-nature",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "description-lot",
    "description-proc",
    "title-lot",
    "identifier-lot",
    "notice-identifier",
    "notice-version",
    "form-type",
    "official-language",
    "links",
]

# `notice-title` is composed by TED as "<Country> – <CPV label> – <real title>".
# Splitting on the en dash recovers the buyer's own wording.
TED_TITLE_SEPARATOR = " – "

# Below this length a lot title is almost always an internal reference
# ("DMOW/MT/03110 - 1", "WS2848982494 - 1") rather than a description.
MIN_MEANINGFUL_TITLE_LEN = 25

# Above this length it is the opposite problem: some buyers paste the entire object
# description into the lot title ("La presente consultation a pour objet ... 300 chars").
# Neither extreme is a usable inbox title, so both ends fall back to `notice-title`.
MAX_MEANINGFUL_TITLE_LEN = 180

# TED `form-type` is the reliable discriminator; `notice-type` is the finer subtype.
FORM_TYPE_TO_NOTICE_TYPE = {
    "planning": NoticeType.PLANNING,
    "competition": NoticeType.COMPETITION,
    "result": NoticeType.RESULT,
    "dir-awa-pre": NoticeType.RESULT,
    "cont-modif": NoticeType.RESULT,
}

# Fallback when `form-type` is absent: match on the notice-type prefix.
NOTICE_TYPE_PREFIXES = (
    ("pin", NoticeType.PLANNING),
    ("cn", NoticeType.COMPETITION),
    ("can", NoticeType.RESULT),
    ("veat", NoticeType.RESULT),
)

PROCEDURE_TYPES = {
    "open": "open",
    "restricted": "restricted",
    "neg-w-call": "negotiated",
    "neg-wo-call": "negotiated",
    "negotiated": "negotiated",
    "comp-dial": "competitive-dialogue",
    "innovation": "innovation-partnership",
    "oth-single": "other",
    "oth-mult": "other",
}


def _first(value: Any) -> Any:
    """TED repeats scalars per lot; the first occurrence is the notice-level value."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_offset_date(value: Any) -> datetime | None:
    """Parse `2026-08-11+02:00` (date + offset) or a full ISO instant, into UTC.

    A bare date carrying an offset is meaningless as an instant, so it is anchored at
    local midnight - correct for `published_at`, and never used for a deadline.
    """
    text = _first(value)
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip().replace("Z", "+00:00")
    candidates = [text]
    # Date-with-offset: insert a midnight time component.
    if "T" not in text and len(text) > 10:
        candidates.insert(0, f"{text[:10]}T00:00:00{text[10:]}")
    elif "T" not in text:
        candidates.insert(0, f"{text}T00:00:00+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    return None


def _combine_date_time(date_value: Any, time_value: Any) -> datetime | None:
    """Rebuild a lot deadline from its split date and time parts.

    The offset on the *time* part is the buyer's clock, and the buyer's clock is what
    governs a submission deadline (SPEC §5) - so it wins over the date's offset.
    """
    date_text = _first(date_value)
    time_text = _first(time_value)
    if not isinstance(date_text, str) or not isinstance(time_text, str):
        return None
    day = date_text.strip()[:10]
    clock = time_text.strip().replace("Z", "+00:00")
    if not day or not clock:
        return None
    try:
        parsed = datetime.fromisoformat(f"{day}T{clock}")
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _dedupe(values: Any) -> list[str]:
    out: list[str] = []
    for item in values if isinstance(values, list) else [values]:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def _amount(value: Any) -> float | None:
    raw = _first(value)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@register
class TedAdapter(BaseAdapter):
    name = "ted"
    version = "1.1.0"

    #: Countries whose notices we ingest. Widening this is a config change (SPEC §4.1).
    DEFAULT_COUNTRIES = ("FRA", "BEL", "LUX")

    #: TED caps a single response; the window is paged until exhausted.
    PAGE_SIZE = 100

    def _query(self, cursor: Cursor) -> str:
        countries = self.config.options.get("countries", self.DEFAULT_COUNTRIES)
        lookback_days = int(self.config.options.get("lookback_days", 2))
        since = cursor.since or datetime.now(UTC) - timedelta(days=lookback_days)
        days_back = max(0, (datetime.now(UTC).date() - since.date()).days)
        # `today(-N)` is TED expert-query syntax; a window (not a sort) is how we bound work.
        return (
            f"(place-of-performance IN ({' '.join(countries)})) AND (publication-date >= today(-{days_back}))"
        )

    def fetch_since(self, cursor: Cursor) -> Iterator[RawNotice]:
        page = 1
        seen_total: int | None = None
        while True:
            body = {
                "query": self._query(cursor),
                "page": page,
                "limit": self.PAGE_SIZE,
                "fields": TED_FIELDS,
            }
            payload = self.request("POST", TED_SEARCH_URL, json=body).json()
            notices = payload.get("notices") or []
            if seen_total is None:
                seen_total = payload.get("totalNoticeCount") or 0
            for notice in notices:
                external_id = notice.get("publication-number")
                if not external_id:
                    # Without a stable id we cannot dedupe or re-fetch: skip rather than
                    # invent one, and let the yield count expose the gap.
                    continue
                yield self.raw(
                    external_id=str(external_id),
                    payload=notice,
                    url=f"https://ted.europa.eu/en/notice/{external_id}",
                )
            if len(notices) < self.PAGE_SIZE:
                return
            page += 1

    def normalize(self, raw: RawNotice) -> CanonicalNotice:
        payload = raw.payload
        mapped = set(TED_FIELDS)
        unmapped = sorted(k for k in payload if k not in mapped)

        language = (
            lang2(self.config.options.get("language"))
            or lang2(_first(payload.get("official-language")))
            or self._detect_language(payload)
        )

        title = self._title(payload, language) or f"TED {raw.external_id}"
        description = pick_lang(payload.get("description-lot"), language) or pick_lang(
            payload.get("description-proc"), language
        )

        places = _dedupe(payload.get("place-of-performance"))
        # A 3-letter all-alpha token here is a country, not a region.
        nuts = [p for p in places if not (len(p) == 3 and p.isalpha())]
        country = (
            country2(_first(payload.get("buyer-country")))
            or country2(_first(payload.get("organisation-country-buyer")))
            or self._country_from_places(places)
            or self.config.country
        )

        deadline = _combine_date_time(
            payload.get("deadline-receipt-tender-date-lot"),
            payload.get("deadline-receipt-tender-time-lot"),
        ) or _parse_offset_date(payload.get("deadline-receipt-request"))

        currency = _first(payload.get("estimated-value-cur-lot"))
        return CanonicalNotice(
            source_refs=[
                SourceRef(
                    source=raw.source,
                    external_id=raw.external_id,
                    url=raw.url,
                    fetched_at=raw.fetched_at,
                )
            ],
            notice_type=self._notice_type(payload),
            country=country,
            language=language,
            buyer=Buyer(
                name=pick_lang(payload.get("buyer-name"), language)
                or pick_lang(payload.get("organisation-name-buyer"), language)
            ),
            title=title,
            description=description,
            cpv=_dedupe(payload.get("classification-cpv")),
            nuts=nuts,
            procedure=Procedure(
                type=PROCEDURE_TYPES.get(str(_first(payload.get("procedure-type")) or "")),
                national_label=_first(payload.get("procedure-type")),
                contract_nature=[
                    n
                    for n in _dedupe(payload.get("contract-nature"))
                    if n in ("works", "supplies", "services")
                ],
            ),
            lots=self._lots(payload, language),
            amounts=Amounts(
                estimated_total=_amount(payload.get("estimated-value-lot")),
                currency=currency if isinstance(currency, str) else None,
                # TED estimates are VAT-exclusive by definition, but the payload does not
                # say so; per §6.2 we do not assert what the source does not state.
                vat=None,
            ),
            dates=Dates(
                published_at=_parse_offset_date(payload.get("publication-date")),
                deadline_at=deadline,
            ),
            urls=Urls(notice=raw.url, documents=self._xml_link(payload)),
            provenance=Provenance(
                adapter=self.adapter_ref,
                normalized_at=datetime.now(UTC),
                raw_content_hash=raw.content_hash,
                unmapped_fields=unmapped,
            ),
            version=int(_first(payload.get("notice-version")) or 1),
        )

    @classmethod
    def _title(cls, payload: dict[str, Any], language: str | None) -> str | None:
        """Pick the most informative title available.

        Neither candidate is reliable alone: `title-lot` is sometimes an internal
        reference ("WS2848982494 - 1") and sometimes the entire object description, while
        `notice-title` is TED's composed "<Country> – <CPV label> – <real title>". So take
        the lot title when its length suggests it is actually a title, and otherwise strip
        the composed prefix off `notice-title`.
        """
        lot_title = pick_lang(payload.get("title-lot"), language)
        if lot_title and MIN_MEANINGFUL_TITLE_LEN <= len(lot_title) <= MAX_MEANINGFUL_TITLE_LEN:
            return lot_title
        notice_title = pick_lang(payload.get("notice-title"), language)
        if notice_title:
            parts = notice_title.split(TED_TITLE_SEPARATOR)
            # Country and CPV label are the first two segments; a title may itself
            # contain the separator, so rejoin everything after them.
            if len(parts) >= 3:
                stripped = TED_TITLE_SEPARATOR.join(parts[2:]).strip()
                if stripped:
                    return stripped
            return notice_title
        return lot_title

    @staticmethod
    def _detect_language(payload: dict[str, Any]) -> str | None:
        """Infer the document language from which keys a multilingual field actually has.

        A notice published in France carries `fra`; the other keys are TED's machine
        translations. Only a single-key map is conclusive.
        """
        for key in ("title-lot", "description-lot"):
            value = payload.get(key)
            if isinstance(value, dict) and len(value) == 1:
                return lang2(next(iter(value)))
        return None

    @staticmethod
    def _country_from_places(places: list[str]) -> str | None:
        for place in places:
            if len(place) == 3 and place.isalpha():
                return ISO3_TO_COUNTRY2.get(place.upper())
        for place in places:  # fall back to the NUTS prefix
            if len(place) >= 2 and place[:2].isalpha():
                return place[:2].upper()
        return None

    @staticmethod
    def _notice_type(payload: dict[str, Any]) -> NoticeType:
        form_type = str(_first(payload.get("form-type")) or "").lower()
        if form_type in FORM_TYPE_TO_NOTICE_TYPE:
            return FORM_TYPE_TO_NOTICE_TYPE[form_type]
        notice_type = str(_first(payload.get("notice-type")) or "").lower()
        for prefix, mapped in NOTICE_TYPE_PREFIXES:
            if notice_type.startswith(prefix):
                return mapped
        # Unknown subtype: treat as a competition so it stays visible to matching rather
        # than being silently dropped. Deliberate: a false positive costs a glance, a
        # false negative costs the tender.
        return NoticeType.COMPETITION

    def _lots(self, payload: dict[str, Any], language: str | None) -> list[Lot]:
        ids = [i for i in (payload.get("identifier-lot") or []) if isinstance(i, str)]
        if not ids:
            return []
        titles = payload.get("title-lot")
        values = payload.get("estimated-value-lot") or []
        currencies = payload.get("estimated-value-cur-lot") or []
        lots: list[Lot] = []
        for index, lot_id in enumerate(dict.fromkeys(ids)):
            amount = values[index] if isinstance(values, list) and index < len(values) else None
            currency = currencies[index] if isinstance(currencies, list) and index < len(currencies) else None
            lots.append(
                Lot(
                    lot_id=lot_id,
                    # Per-lot titles are only separable when TED returns one per lot;
                    # with a single shared title we reuse it rather than mis-attribute.
                    title=pick_lang(titles, language),
                    amount_est=_amount(amount),
                    currency=currency if isinstance(currency, str) else None,
                )
            )
        return lots

    @staticmethod
    def _xml_link(payload: dict[str, Any]) -> str | None:
        """The multilingual XML rendition - the machine-readable full notice."""
        links = payload.get("links")
        if not isinstance(links, dict):
            return None
        xml = links.get("xml")
        if isinstance(xml, dict):
            for value in xml.values():
                if isinstance(value, str):
                    return value
        return None
