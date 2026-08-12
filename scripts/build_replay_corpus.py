#!/usr/bin/env python3
"""Build the 48h replay corpus used by the matching acceptance tests (SPEC §8.4, Annex E.3).

Why this is a *normalized* corpus rather than raw payloads: a genuine 48h FR/BE/LU window
is ~1,100 notices, and their raw eForms payloads weigh ~15 MB - too heavy to carry in git
for every clone and CI run. Canonical notices are ~20x smaller and are exactly what
matching and clustering consume.

The division of labour between fixture kinds:
  * fixtures/notices/{ted,boamp}_*.json  - small, pretty-printed, RAW.
    Pin adapter behaviour; a source changing shape shows up in the diff.
  * fixtures/notices/replay_48h.json     - large, compact, NORMALIZED.
    Pins matching/clustering behaviour at realistic volume.

Usage:
    python scripts/build_replay_corpus.py            # full 48h window
    python scripts/build_replay_corpus.py --days 7   # wider window
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "ingestion"))
sys.path.insert(0, str(REPO_ROOT))

from bidpilot_ingestion.adapters import (  # noqa: E402 - path set above
    BoampAdapter,
    LegalBasis,
    SourceConfig,
    TedAdapter,
)
from bidpilot_ingestion.canonical import CanonicalNotice, RawNotice  # noqa: E402
from scripts.capture_fixtures import TED_FIELDS  # noqa: E402

USER_AGENT = "BidPilotBot/1.0 (+https://bidpilot.example/bot)"
TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
BOAMP_URL = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
OUTPUT = REPO_ROOT / "fixtures" / "notices" / "replay_48h.json"

# Safety ceiling so a bad window cannot pull the whole archive into a fixture.
MAX_PAGES = 15
PAGE_SIZE = 100


def _post(url: str, payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def _get(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))


def collect_ted(days: int) -> list[CanonicalNotice]:
    adapter = TedAdapter(
        SourceConfig(
            code="eu-ted",
            country="EU",
            tier=1,
            kind="api",
            legal=LegalBasis(basis="open-license", notes="TED reuse with source acknowledgment"),
        )
    )
    notices: list[CanonicalNotice] = []
    for page in range(1, MAX_PAGES + 1):
        payload = _post(
            TED_SEARCH_URL,
            {
                "query": (
                    "(place-of-performance IN (FRA BEL LUX)) "
                    f"AND (publication-date >= today(-{days}))"
                ),
                "page": page,
                "limit": PAGE_SIZE,
                "fields": TED_FIELDS,
            },
        )
        batch = payload.get("notices") or []
        for entry in batch:
            external_id = entry.get("publication-number")
            if not external_id:
                continue
            notices.append(
                adapter.normalize(
                    RawNotice(
                        source="eu-ted",
                        external_id=str(external_id),
                        payload=entry,
                        url=f"https://ted.europa.eu/en/notice/{external_id}",
                    )
                )
            )
        print(f"  ted page {page}: +{len(batch)} (total {len(notices)})")
        if len(batch) < PAGE_SIZE:
            break
    return notices


def collect_boamp(days: int) -> list[CanonicalNotice]:
    adapter = BoampAdapter(
        SourceConfig(
            code="fr-boamp",
            country="FR",
            tier=1,
            kind="api",
            legal=LegalBasis(basis="open-license", notes="licence etalab-2.0"),
        )
    )
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    notices: list[CanonicalNotice] = []
    for page in range(MAX_PAGES):
        params = {
            "limit": PAGE_SIZE,
            "offset": page * PAGE_SIZE,
            "order_by": "dateparution desc",
            "where": f"dateparution >= date'{since}'",
        }
        payload = _get(f"{BOAMP_URL}?{urllib.parse.urlencode(params)}")
        batch = payload.get("results") or []
        for record in batch:
            external_id = record.get("idweb") or record.get("id")
            if not external_id:
                continue
            notices.append(
                adapter.normalize(
                    RawNotice(
                        source="fr-boamp",
                        external_id=str(external_id),
                        payload=record,
                        url=record.get("url_avis"),
                    )
                )
            )
        print(f"  boamp page {page + 1}: +{len(batch)} (total {len(notices)})")
        if len(batch) < PAGE_SIZE:
            break
    return notices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=2, help="window width in days (default 2 = 48h)")
    args = parser.parse_args()

    print(f"building {args.days}-day replay corpus …")
    notices = [*collect_ted(args.days), *collect_boamp(args.days)]
    if not notices:
        print("no notices collected; leaving the existing corpus untouched")
        return 1

    corpus = {
        "window_days": args.days,
        "captured_at": datetime.now(UTC).isoformat(),
        "count": len(notices),
        # `mode="json"` so datetimes serialise as ISO strings and the corpus round-trips.
        "notices": [notice.model_dump(mode="json") for notice in notices],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, ensure_ascii=False) + "\n")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}: {len(notices)} notices, {OUTPUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
