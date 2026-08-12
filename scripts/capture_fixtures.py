#!/usr/bin/env python3
"""Capture real source payloads into /fixtures for adapter regression tests.

Fixtures are the contract between us and sources we do not control (SPEC §21.1:
"Every adapter lands with: fixture snapshots, parser regression test").
Re-run this script when a source changes shape; review the diff before committing.

Usage:
    python scripts/capture_fixtures.py            # capture everything
    python scripts/capture_fixtures.py ted boamp  # capture selected sources
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
USER_AGENT = "BidPilotBot/1.0 (+https://bidpilot.example/bot)"

# Fields verified against the live TED API field catalogue (1830 supported values).
# Keep this list in sync with services/ingestion/bidpilot_ingestion/adapters/ted.py.
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


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def _get_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            # Opendatasoft gzips larger responses, and urllib does not decompress;
            # asking for identity keeps the capture simple and the payload verbatim.
            "Accept-Encoding": "identity",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))


def _write(rel_path: str, data: Any, *, compact: bool = False) -> None:
    """Small fixtures are pretty-printed so a source's shape change shows up in the diff.
    Bulk replay corpora are written compact - nobody reviews 300 notices by eye."""
    target = FIXTURES / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    indent = None if compact else 2
    target.write_text(json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True) + "\n")
    print(f"  wrote {rel_path} ({target.stat().st_size / 1024:.1f} KB)")


def capture_ted() -> None:
    """TED v3 expert search. No API key required for search (SPEC §6.3, verified)."""
    print("TED …")
    for name, query in (
        ("competition", "(place-of-performance IN (FRA BEL LUX)) AND (notice-type IN (cn-standard))"),
        ("award", "(place-of-performance IN (FRA BEL LUX)) AND (notice-type IN (can-standard))"),
        ("planning", "(place-of-performance IN (FRA BEL LUX)) AND (notice-type IN (pin-only))"),
    ):
        try:
            data = _post_json(
                "https://api.ted.europa.eu/v3/notices/search",
                {"query": f"{query} AND (publication-date >= today(-7))", "limit": 8, "page": 1, "fields": TED_FIELDS},
            )
        except Exception as exc:  # noqa: BLE001 - fixture capture is best-effort per source
            print(f"  !! {name}: {exc}")
            continue
        _write(f"notices/ted_{name}.json", data)


def capture_boamp() -> None:
    """BOAMP via Opendatasoft Explore v2.1, licence etalab-2.0 (SPEC §6.3, verified)."""
    print("BOAMP …")
    base = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
    for name, params in (
        ("recent", {"limit": 8, "order_by": "dateparution desc"}),
        # `titulaire` is populated on award notices - the DECP-adjacent signal for §14.
        ("awards", {"limit": 5, "order_by": "dateparution desc", "where": "titulaire is not null"}),
    ):
        try:
            data = _get_json(f"{base}?{urllib.parse.urlencode(params)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {name}: {exc}")
            continue
        _write(f"notices/boamp_{name}.json", data)


def capture_entreprises() -> None:
    """Recherche d'entreprises API - free, no key. Powers SIRET bootstrap (SPEC §7.1)."""
    print("Recherche d'entreprises …")
    base = "https://recherche-entreprises.api.gouv.fr/search"
    for name, q in (("ipsos", "ipsos"), ("small_it", "societe informatique")):
        try:
            data = _get_json(f"{base}?{urllib.parse.urlencode({'q': q, 'per_page': 3})}")
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {name}: {exc}")
            continue
        _write(f"entreprises/{name}.json", data)


CAPTURES = {
    "ted": capture_ted,
    "boamp": capture_boamp,
    "entreprises": capture_entreprises,
}


def main() -> int:
    wanted = sys.argv[1:] or list(CAPTURES)
    unknown = [w for w in wanted if w not in CAPTURES]
    if unknown:
        print(f"unknown source(s): {', '.join(unknown)}; known: {', '.join(CAPTURES)}")
        return 2
    for key in wanted:
        CAPTURES[key]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
