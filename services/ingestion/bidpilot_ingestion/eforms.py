"""Helpers for eForms/UBL payloads.

Above EU thresholds, publication on TED is mandatory in the eForms standard (SPEC §5) -
and BOAMP now embeds the same eForms UBL tree inside its `donnees` field. So this module
is shared by both adapters instead of being duplicated per source.

Two shapes have to be tolerated:
  * TED Search API v3 - flat, one key per eForms business term, values often multilingual
    maps keyed by ISO-639-3 (`{"fra": [...]}`) and often duplicated across lots.
  * UBL JSON (BOAMP `donnees`) - the nested `cbc:`/`cac:`/`efac:` tree, where a repeated
    element is a dict when it occurs once and a list when it occurs twice.

Everything here is defensive by design: a source that changes shape must degrade to None,
never raise, because one malformed notice must not stall a whole ingestion run.
"""

from __future__ import annotations

from typing import Any

# ISO-639-3 -> ISO-639-1. eForms uses 3-letter codes; our canonical model uses 2-letter.
ISO3_TO_ISO2 = {
    "fra": "fr",
    "eng": "en",
    "nld": "nl",
    "deu": "de",
    "ger": "de",
    "ita": "it",
    "spa": "es",
    "por": "pt",
    "dan": "da",
    "swe": "sv",
    "fin": "fi",
    "pol": "pl",
    "ces": "cs",
    "slk": "sk",
    "slv": "sl",
    "hun": "hu",
    "ron": "ro",
    "bul": "bg",
    "ell": "el",
    "hrv": "hr",
    "est": "et",
    "lav": "lv",
    "lit": "lt",
    "mlt": "mt",
    "gle": "ga",
    "nor": "no",
}

# ISO-3166 alpha-3 -> alpha-2, limited to the launch geographies plus EU neighbours
# we already ingest through TED (SPEC §4.1).
ISO3_TO_COUNTRY2 = {
    "FRA": "FR",
    "BEL": "BE",
    "LUX": "LU",
    "NLD": "NL",
    "DEU": "DE",
    "ITA": "IT",
    "ESP": "ES",
    "PRT": "PT",
    "IRL": "IE",
    "AUT": "AT",
    "POL": "PL",
    "DNK": "DK",
    "SWE": "SE",
    "FIN": "FI",
    "CZE": "CZ",
    "SVK": "SK",
    "SVN": "SI",
    "HUN": "HU",
    "ROU": "RO",
    "BGR": "BG",
    "GRC": "GR",
    "HRV": "HR",
    "EST": "EE",
    "LVA": "LV",
    "LTU": "LT",
    "MLT": "MT",
    "CYP": "CY",
    "GBR": "GB",
    "NOR": "NO",
    "CHE": "CH",
}

# Preference order when a multilingual field carries no usable document language.
# French first: the launch geographies are FR/BE/LU (SPEC §4.3).
LANG_PREFERENCE = ("fra", "eng", "nld", "deu")


def country2(code: str | None) -> str | None:
    """Normalise a country code of unknown width to ISO-3166 alpha-2."""
    if not code:
        return None
    c = str(code).strip().upper()
    if len(c) == 2 and c.isalpha():
        return c
    return ISO3_TO_COUNTRY2.get(c)


def lang2(code: str | None) -> str | None:
    """Normalise a language code of unknown width to ISO-639-1."""
    if not code:
        return None
    c = str(code).strip().lower()
    if len(c) == 2:
        return c
    return ISO3_TO_ISO2.get(c)


def pick_lang(value: Any, prefer: str | None = None) -> str | None:
    """Collapse a possibly-multilingual eForms value to a single string.

    TED returns `notice-title` as `{"fra": "..."}` but `description-lot` as
    `{"fra": ["..."]}`, and plain strings elsewhere. All three arrive here.

    `prefer` is the notice's own document language: showing a French notice in French
    matters more than any global default.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            got = pick_lang(item, prefer)
            if got:
                return got
        return None
    if isinstance(value, dict):
        order: list[str] = []
        if prefer:
            # Accept the preferred language in either width.
            order += [prefer, *[k for k, v in ISO3_TO_ISO2.items() if v == prefer]]
        order += list(LANG_PREFERENCE)
        for key in order:
            if key in value:
                got = pick_lang(value[key], None)
                if got:
                    return got
        for item in value.values():  # any language beats nothing
            got = pick_lang(item, None)
            if got:
                return got
    return None


def as_list(value: Any) -> list[Any]:
    """UBL emits a bare dict for a single occurrence and a list for several."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_of(value: Any) -> str | None:
    """Read a UBL scalar, which may be `"x"` or `{"@listName": ..., "#text": "x"}`."""
    if value is None:
        return None
    if isinstance(value, dict):
        got = value.get("#text")
        return str(got).strip() or None if got is not None else None
    if isinstance(value, list):
        for item in value:
            got = text_of(item)
            if got:
                return got
        return None
    text = str(value).strip()
    return text or None


def get_path(node: Any, *keys: str) -> Any:
    """Walk a UBL tree, transparently stepping into the first item of any repeated element.

    `get_path(cn, "cac:ProcurementProject", "cbc:Name")` reads the same whether
    ProcurementProject occurred once (dict) or twice (list).
    """
    current = node
    for key in keys:
        if isinstance(current, list):
            current = current[0] if current else None
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def iter_nodes(node: Any, key: str) -> list[Any]:
    """Every node reachable under `key`, at any depth, flattened.

    eForms nests the same business term at different depths depending on notice subtype,
    so a depth-agnostic sweep is more robust than a hardcoded path.
    """
    found: list[Any] = []

    def walk(current: Any) -> None:
        if isinstance(current, dict):
            for k, v in current.items():
                if k == key:
                    found.extend(as_list(v))
                else:
                    walk(v)
        elif isinstance(current, list):
            for item in current:
                walk(item)

    walk(node)
    return found


def cpv_codes(node: Any) -> list[str]:
    """CPV codes from `cbc:ItemClassificationCode` entries, deduplicated, order kept."""
    out: list[str] = []
    for raw in iter_nodes(node, "cbc:ItemClassificationCode"):
        if isinstance(raw, dict) and raw.get("@listName") not in (None, "cpv"):
            continue
        code = text_of(raw)
        if not code:
            continue
        digits = "".join(ch for ch in code if ch.isdigit())[:8]
        if len(digits) == 8 and digits not in out:
            out.append(digits)
    return out


def nuts_codes(node: Any) -> list[str]:
    """NUTS codes from `cbc:CountrySubentityCode` entries (listName="nuts")."""
    out: list[str] = []
    for raw in iter_nodes(node, "cbc:CountrySubentityCode"):
        if isinstance(raw, dict) and raw.get("@listName") not in (None, "nuts"):
            continue
        code = text_of(raw)
        if code and code.upper() not in out:
            out.append(code.upper())
    return out


def company_ids(node: Any) -> list[str]:
    """`cac:PartyLegalEntity/cbc:CompanyID` values - SIRET (14) or SIREN (9) in France."""
    out: list[str] = []
    for entity in iter_nodes(node, "cac:PartyLegalEntity"):
        code = text_of(get_path(entity, "cbc:CompanyID"))
        if code:
            digits = "".join(ch for ch in code if ch.isdigit())
            if digits and digits not in out:
                out.append(digits)
    return out
