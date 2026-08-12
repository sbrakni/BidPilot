"""Shared fixtures. Every adapter test runs against **real captured payloads**.

Invented fixtures test our imagination; real ones test the sources. The files under
/fixtures were captured from the live APIs by scripts/capture_fixtures.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bidpilot_ingestion.adapters import BoampAdapter, LegalBasis, SourceConfig, TedAdapter
from bidpilot_ingestion.adapters.base import content_hash
from bidpilot_ingestion.canonical import CanonicalNotice, RawNotice

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures"


def load_fixture(relative: str) -> Any:
    return json.loads((FIXTURES / relative).read_text())


@pytest.fixture(scope="session")
def ted_adapter() -> TedAdapter:
    return TedAdapter(
        SourceConfig(
            code="eu-ted",
            country="EU",
            tier=1,
            kind="api",
            legal=LegalBasis(basis="open-license", notes="TED reuse with source acknowledgment"),
        )
    )


@pytest.fixture(scope="session")
def boamp_adapter() -> BoampAdapter:
    return BoampAdapter(
        SourceConfig(
            code="fr-boamp",
            country="FR",
            tier=1,
            kind="api",
            legal=LegalBasis(basis="open-license", notes="licence etalab-2.0"),
        )
    )


def ted_raws(name: str = "ted_competition") -> list[RawNotice]:
    payload = load_fixture(f"notices/{name}.json")
    return [
        RawNotice(
            source="eu-ted",
            external_id=str(notice["publication-number"]),
            payload=notice,
            url=f"https://ted.europa.eu/en/notice/{notice['publication-number']}",
            content_hash=content_hash(notice),
        )
        for notice in payload["notices"]
        if notice.get("publication-number")
    ]


def boamp_raws(name: str = "boamp_recent") -> list[RawNotice]:
    payload = load_fixture(f"notices/{name}.json")
    return [
        RawNotice(
            source="fr-boamp",
            external_id=str(record["idweb"]),
            payload=record,
            url=record.get("url_avis"),
            content_hash=content_hash(record),
        )
        for record in payload["results"]
        if record.get("idweb")
    ]


@pytest.fixture(scope="session")
def ted_notices(ted_adapter: TedAdapter) -> list[CanonicalNotice]:
    return [ted_adapter.normalize(raw) for raw in ted_raws()]


@pytest.fixture(scope="session")
def boamp_notices(boamp_adapter: BoampAdapter) -> list[CanonicalNotice]:
    return [boamp_adapter.normalize(raw) for raw in boamp_raws()]
