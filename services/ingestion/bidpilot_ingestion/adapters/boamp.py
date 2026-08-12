"""BOAMP (France) adapter - the national gazette (SPEC §6.3).

Source: Opendatasoft Explore v2.1, dataset `boamp`, licence etalab-2.0.
Verified against the live API (2026-08):
  * `GET .../catalog/datasets/boamp/records` with `limit`, `offset`, `order_by`, `where`.
  * Default order is *oldest first*; `order_by=dateparution desc` is required for
    incremental fetch. ~1.68M records total.
  * Flat fields are dependable: `idweb`, `objet`, `nomacheteur`, `datelimitereponse`,
    `dateparution`, `code_departement`, `url_avis`, `nature_libelle`, `titulaire`.
  * `donnees` is a **JSON string**. Recent records contain the full eForms UBL tree
    (`{"EFORMS": {"ContractNotice": {...}}}`); older records use a legacy
    `{"IDENTITE": {...}}` shape. Both are handled - a 2015 notice must still normalise,
    because the archive is what powers the renewal radar (SPEC §14.2).

Design: flat fields carry the core notice; `donnees` is mined for what only eForms has
(CPV, NUTS, buyer SIRET, lots). Cheap, and resilient when one of the two shapes changes.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from ..canonical import (
    Address,
    Amounts,
    Award,
    Buyer,
    CanonicalNotice,
    Contact,
    Dates,
    DocumentKind,
    DocumentRef,
    Lot,
    NoticeType,
    Procedure,
    Provenance,
    RawNotice,
    SourceRef,
    Urls,
)
from ..eforms import company_ids, cpv_codes, get_path, iter_nodes, nuts_codes, pick_lang, text_of
from .base import BaseAdapter, Cursor, register

BOAMP_RECORDS_URL = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"

# France is one timezone; BOAMP timestamps carry an offset but the buyer's clock is Paris.
FR_TIMEZONE = "Europe/Paris"

# `nature_libelle` is the human label of the notice family. Award notices name a
# `titulaire`, which is the signal the renewal radar consumes (SPEC §14.2).
NATURE_TO_NOTICE_TYPE = {
    "avis de marché": NoticeType.COMPETITION,
    "avis de marche": NoticeType.COMPETITION,
    "avis d'attribution": NoticeType.RESULT,
    "avis d attribution": NoticeType.RESULT,
    "résultat de marché": NoticeType.RESULT,
    "resultat de marche": NoticeType.RESULT,
    "avis de pré-information": NoticeType.PLANNING,
    "avis de pre-information": NoticeType.PLANNING,
    "avis de préinformation": NoticeType.PLANNING,
    "programme prévisionnel": NoticeType.PLANNING,
}

PROCEDURE_LABEL_TO_TYPE = {
    "ouvert": "open",
    "appel d'offres ouvert": "open",
    "restreint": "restricted",
    "appel d'offres restreint": "restricted",
    "négocié": "negotiated",
    "procédure négociée": "negotiated",
    "concurrentielle avec négociation": "negotiated",
    "dialogue compétitif": "competitive-dialogue",
    "procédure adaptée": "adapted",
    "adapté": "adapted",
    "mapa": "adapted",
}

# The `donnees` keys we deliberately read. Anything else in there is reported as unmapped.
_KNOWN_FLAT_FIELDS = frozenset(
    {
        "idweb",
        "objet",
        "nomacheteur",
        "datelimitereponse",
        "dateparution",
        "datefindiffusion",
        "code_departement",
        "code_departement_prestation",
        "descripteur_code",
        "descripteur_libelle",
        "famille",
        "famille_libelle",
        "nature",
        "nature_libelle",
        "nature_categorise",
        "nature_categorise_libelle",
        "procedure_libelle",
        "procedure_categorise",
        "type_procedure",
        "soustype_procedure",
        "sousnature",
        "sousnature_libelle",
        "type_avis",
        "type_marche",
        "type_marche_facette",
        "url_avis",
        "titulaire",
        "donnees",
        "dc",
        "id",
        "etat",
        "filename",
        "gestion",
        "perimetre",
        "source_schema",
        "annonce_lie",
        "annonces_anterieures",
        "contractfolderid",
        "criteres",
        "marche_public_simplifie",
        "marche_public_simplifie_label",
    }
)


_WHITESPACE = re.compile(r"\s+")


def clean_text(value: Any) -> str | None:
    """Decode HTML entities and collapse whitespace.

    BOAMP serves entity-encoded text (`Communauté d&#039;Agglomération`). Left raw it
    reaches the UI, the embeddings and the generated mémoire - so it is fixed on ingest,
    once, rather than papered over at each render site.
    """
    if value is None:
        return None
    text = html.unescape(str(value))
    # Double-encoded values (`&amp;#039;`) appear occasionally; one more pass settles them.
    if "&" in text:
        text = html.unescape(text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def _parse_instant(value: Any) -> datetime | None:
    """Parse an ISO date or timestamp to UTC. Returns None rather than guessing."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if "T" not in text and len(text) == 10:
        text = f"{text}T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(i).strip() for i in items if i is not None and str(i).strip()]


def dept_to_nuts(dept: str) -> str | None:
    """Map a French département code to its NUTS-2 region.

    BOAMP publishes départements, matching needs NUTS (SPEC §8.1 filters on NUTS), so
    without this bridge every BOAMP notice would fail a geographic filter. Overseas
    départements are NUTS-2 in their own right; Corsica is FRM0.
    """
    code = dept.strip().upper().zfill(2) if dept.strip().isdigit() else dept.strip().upper()
    return _DEPT_NUTS2.get(code)


# Département -> NUTS 2021 level-2 region. Reference data, kept as data (SPEC §4.2).
_DEPT_NUTS2 = {
    # Île-de-France
    **{d: "FR10" for d in ("75", "77", "78", "91", "92", "93", "94", "95")},
    # Centre-Val de Loire
    **{d: "FRB0" for d in ("18", "28", "36", "37", "41", "45")},
    # Bourgogne-Franche-Comté
    **{d: "FRC1" for d in ("21", "58", "71", "89")},
    **{d: "FRC2" for d in ("25", "39", "70", "90")},
    # Normandie
    **{d: "FRD1" for d in ("14", "50", "61")},
    **{d: "FRD2" for d in ("27", "76")},
    # Hauts-de-France
    **{d: "FRE1" for d in ("59", "62")},
    **{d: "FRE2" for d in ("02", "60", "80")},
    # Grand Est
    **{d: "FRF1" for d in ("67", "68")},
    **{d: "FRF2" for d in ("08", "10", "51", "52")},
    **{d: "FRF3" for d in ("54", "55", "57", "88")},
    # Pays de la Loire
    **{d: "FRG0" for d in ("44", "49", "53", "72", "85")},
    # Bretagne
    **{d: "FRH0" for d in ("22", "29", "35", "56")},
    # Nouvelle-Aquitaine
    **{d: "FRI1" for d in ("24", "33", "40", "47", "64")},
    **{d: "FRI2" for d in ("19", "23", "87")},
    **{d: "FRI3" for d in ("16", "17", "79", "86")},
    # Occitanie
    **{d: "FRJ1" for d in ("11", "30", "34", "48", "66")},
    **{d: "FRJ2" for d in ("09", "12", "31", "32", "46", "65", "81", "82")},
    # Auvergne-Rhône-Alpes
    **{d: "FRK1" for d in ("03", "15", "43", "63")},
    **{d: "FRK2" for d in ("01", "07", "26", "38", "42", "69", "73", "74")},
    # Provence-Alpes-Côte d'Azur
    **{d: "FRL0" for d in ("04", "05", "06", "13", "83", "84")},
    # Corse
    **{d: "FRM0" for d in ("2A", "2B", "20")},
    # Outre-mer
    "971": "FRY1",
    "972": "FRY2",
    "973": "FRY3",
    "974": "FRY4",
    "976": "FRY5",
}


@register
class BoampAdapter(BaseAdapter):
    name = "boamp"
    version = "1.0.0"

    PAGE_SIZE = 100
    # Opendatasoft caps offset-based paging; beyond it we narrow the window instead.
    MAX_OFFSET = 9900

    def fetch_since(self, cursor: Cursor) -> Iterator[RawNotice]:
        offset = 0
        since = cursor.since
        while True:
            params: dict[str, Any] = {
                "limit": self.PAGE_SIZE,
                "offset": offset,
                "order_by": "dateparution desc",
            }
            if since is not None:
                params["where"] = f"dateparution >= date'{since.date().isoformat()}'"
            payload = self.request("GET", BOAMP_RECORDS_URL, params=params).json()
            results = payload.get("results") or []
            for record in results:
                external_id = record.get("idweb") or record.get("id")
                if not external_id:
                    continue
                yield self.raw(
                    external_id=str(external_id),
                    payload=record,
                    url=record.get("url_avis"),
                )
            offset += self.PAGE_SIZE
            if len(results) < self.PAGE_SIZE or offset > self.MAX_OFFSET:
                return

    def normalize(self, raw: RawNotice) -> CanonicalNotice:
        record = raw.payload
        eforms = self._parse_donnees(record.get("donnees"))
        notice_node = self._eforms_notice(eforms)
        legacy = eforms.get("IDENTITE") if isinstance(eforms, dict) else None

        title = (
            clean_text(record.get("objet"))
            or clean_text(pick_lang(get_path(notice_node, "cac:ProcurementProject", "cbc:Name"), "fr"))
            or f"BOAMP {raw.external_id}"
        )

        depts = _as_str_list(record.get("code_departement_prestation")) or _as_str_list(
            record.get("code_departement")
        )
        nuts = nuts_codes(notice_node) if notice_node else []
        for dept in depts:
            mapped = dept_to_nuts(dept)
            if mapped and mapped not in nuts:
                nuts.append(mapped)

        buyer = self._buyer(record, notice_node, legacy)
        notice_type = self._notice_type(record)

        unmapped = sorted(k for k in record if k not in _KNOWN_FLAT_FIELDS)
        return CanonicalNotice(
            source_refs=[
                SourceRef(
                    source=raw.source,
                    external_id=raw.external_id,
                    url=raw.url or record.get("url_avis"),
                    fetched_at=raw.fetched_at,
                )
            ],
            notice_type=notice_type,
            country="FR",
            language="fr",
            buyer=buyer,
            title=str(title),
            description=self._description(record, notice_node),
            cpv=cpv_codes(notice_node) if notice_node else [],
            nuts=nuts,
            procedure=Procedure(
                type=self._procedure_type(record),
                national_label=record.get("procedure_libelle") or record.get("nature_libelle"),
            ),
            lots=self._lots(notice_node) if notice_node else [],
            amounts=Amounts(
                estimated_total=self._estimated_total(notice_node),
                currency=self._currency(notice_node),
                vat=None,
            ),
            dates=Dates(
                published_at=_parse_instant(record.get("dateparution")),
                deadline_at=_parse_instant(record.get("datelimitereponse")),
                tz=FR_TIMEZONE,
            ),
            documents=self._documents(record),
            urls=Urls(notice=record.get("url_avis")),
            # BOAMP publishes the notice; the DCE itself lives on the buyer's profile,
            # which frequently requires an account. We deep-link rather than assume.
            requires_account_for_docs=None,
            award=self._award(record) if notice_type is NoticeType.RESULT else None,
            provenance=Provenance(
                adapter=self.adapter_ref,
                normalized_at=datetime.now(UTC),
                raw_content_hash=raw.content_hash,
                unmapped_fields=unmapped,
            ),
        )

    # ---------------------------------------------------------------- donnees handling

    @staticmethod
    def _parse_donnees(value: Any) -> dict[str, Any]:
        """`donnees` is a JSON *string*; malformed content must not fail the notice."""
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _eforms_notice(eforms: dict[str, Any]) -> dict[str, Any] | None:
        """Unwrap `{"EFORMS": {"<NoticeKind>": {...}}}` to the notice node."""
        block = eforms.get("EFORMS")
        if not isinstance(block, dict):
            return None
        for value in block.values():
            if isinstance(value, dict):
                return value
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
        return None

    # ---------------------------------------------------------------- field extraction

    def _buyer(self, record: dict[str, Any], notice_node: dict[str, Any] | None, legacy: Any) -> Buyer:
        name = record.get("nomacheteur")
        siret = siren = None
        contact = Contact()
        address = Address()

        if notice_node is not None:
            ids = company_ids(notice_node)
            for candidate in ids:
                if len(candidate) == 14 and siret is None:
                    siret = candidate
                elif len(candidate) == 9 and siren is None:
                    siren = candidate
            party = next(iter(iter_nodes(notice_node, "cac:ContractingParty")), None)
            if party is not None:
                name = name or pick_lang(get_path(party, "cac:Party", "cac:PartyName", "cbc:Name"), "fr")
                postal = get_path(party, "cac:Party", "cac:PostalAddress")
                if postal:
                    address = Address(
                        street=text_of(get_path(postal, "cbc:StreetName")),
                        postcode=text_of(get_path(postal, "cbc:PostalZone")),
                        city=text_of(get_path(postal, "cbc:CityName")),
                        nuts=text_of(get_path(postal, "cbc:CountrySubentityCode")),
                        country=text_of(get_path(postal, "cac:Country", "cbc:IdentificationCode")),
                    )
                node = get_path(party, "cac:Party", "cac:Contact")
                if node:
                    contact = Contact(
                        email=text_of(get_path(node, "cbc:ElectronicMail")),
                        phone=text_of(get_path(node, "cbc:Telephone")),
                        website=text_of(get_path(party, "cac:Party", "cbc:WebsiteURI")),
                    )

        if isinstance(legacy, dict):  # pre-eForms archive records
            name = name or legacy.get("DENOMINATION")
            contact = contact or Contact()
            if not contact.email and legacy.get("MEL"):
                contact = Contact(email=legacy.get("MEL"), phone=legacy.get("TEL") or contact.phone)
            if not address.city:
                address = Address(
                    street=legacy.get("ADRESSE"),
                    postcode=legacy.get("CP"),
                    city=legacy.get("VILLE"),
                )

        return Buyer(
            name=clean_text(name),
            siren=siren,
            siret=siret,
            contact=contact,
            address=address,
        )

    @staticmethod
    def _description(record: dict[str, Any], notice_node: dict[str, Any] | None) -> str | None:
        if notice_node is not None:
            described = clean_text(
                pick_lang(get_path(notice_node, "cac:ProcurementProject", "cbc:Description"), "fr")
            )
            if described:
                return described
        labels = _as_str_list(record.get("descripteur_libelle"))
        return ", ".join(labels) if labels else None

    @staticmethod
    def _notice_type(record: dict[str, Any]) -> NoticeType:
        for key in ("nature_libelle", "nature_categorise_libelle", "sousnature_libelle"):
            label = record.get(key)
            if isinstance(label, str):
                mapped = NATURE_TO_NOTICE_TYPE.get(label.strip().lower())
                if mapped:
                    return mapped
        # A named winner is conclusive evidence of a result notice, whatever the label says.
        if record.get("titulaire"):
            return NoticeType.RESULT
        return NoticeType.COMPETITION

    @staticmethod
    def _procedure_type(record: dict[str, Any]) -> str | None:
        for key in ("procedure_libelle", "type_procedure", "soustype_procedure"):
            label = record.get(key)
            if not isinstance(label, str):
                continue
            needle = label.strip().lower()
            if needle in PROCEDURE_LABEL_TO_TYPE:
                return PROCEDURE_LABEL_TO_TYPE[needle]
            for known, mapped in PROCEDURE_LABEL_TO_TYPE.items():
                if known in needle:
                    return mapped
        return None

    @staticmethod
    def _lots(notice_node: dict[str, Any]) -> list[Lot]:
        lots: list[Lot] = []
        for node in iter_nodes(notice_node, "cac:ProcurementProjectLot"):
            lot_id = text_of(get_path(node, "cbc:ID"))
            if not lot_id:
                continue
            project = get_path(node, "cac:ProcurementProject")
            lots.append(
                Lot(
                    lot_id=lot_id,
                    title=clean_text(pick_lang(get_path(project, "cbc:Name"), "fr")),
                    description=clean_text(pick_lang(get_path(project, "cbc:Description"), "fr")),
                    cpv=cpv_codes(project) if project else [],
                    place_nuts=nuts_codes(project) if project else [],
                )
            )
        return lots

    @staticmethod
    def _estimated_total(notice_node: dict[str, Any] | None) -> float | None:
        if notice_node is None:
            return None
        for key in ("cbc:EstimatedOverallContractAmount", "cbc:TotalAmount", "cbc:PayableAmount"):
            for node in iter_nodes(notice_node, key):
                text = text_of(node)
                if text:
                    try:
                        return float(text)
                    except ValueError:
                        continue
        return None

    @staticmethod
    def _currency(notice_node: dict[str, Any] | None) -> str | None:
        if notice_node is None:
            return None
        for key in ("cbc:EstimatedOverallContractAmount", "cbc:TotalAmount", "cbc:PayableAmount"):
            for node in iter_nodes(notice_node, key):
                if isinstance(node, dict):
                    currency = node.get("@currencyID")
                    if isinstance(currency, str) and len(currency) == 3:
                        return currency.upper()
        return None

    @staticmethod
    def _documents(record: dict[str, Any]) -> list[DocumentRef]:
        """BOAMP itself exposes the notice page, not the DCE; we deep-link to it."""
        url = record.get("url_avis")
        if not url:
            return []
        return [DocumentRef(kind=DocumentKind.OTHER, title="Avis BOAMP", url=str(url))]

    @staticmethod
    def _award(record: dict[str, Any]) -> Award | None:
        """Winner details from `titulaire`.

        Observed as a list of bare supplier names, sometimes repeated
        (`["NEEDD NORD", "NEEDD NORD"]`); some records use objects instead. BOAMP does
        not publish the winner's SIREN, so it stays None here and is resolved later
        against DECP, which does (SPEC §14.1) - guessing an identity would mis-attribute
        wins to the wrong company on the competitor pages.
        """
        holders = record.get("titulaire")
        if not holders:
            return None
        first = holders[0] if isinstance(holders, list) else holders
        if isinstance(first, dict):
            name = first.get("denomination") or first.get("nom") or first.get("DENOMINATION")
            siret = first.get("siret") or first.get("SIRET")
            digits = "".join(ch for ch in str(siret) if ch.isdigit()) if siret else ""
            return Award(
                supplier_name=clean_text(name),
                supplier_siren=digits[:9] if len(digits) >= 9 else None,
            )
        name = clean_text(first)
        return Award(supplier_name=name) if name else None
