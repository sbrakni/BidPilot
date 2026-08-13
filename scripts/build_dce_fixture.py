"""Build a multi-page PDF test document from real captured procurement text.

Why this exists, and what it is not:

  * It is **not** a substitute for Annex E.3's gold corpus. That requires ≥ 15 real DCEs with
    hand-labelled requirement registers, which are published on authenticated buyer platforms.
    This produces one document, for testing the *extractor*, not the extraction.
  * The PDF container is synthetic; the prose inside is not. Every paragraph is real text from
    the captured TED/BOAMP corpus (ADR-0001), so the extractor meets genuine French procurement
    language - accented characters, long administrative sentences, the layout of an article list -
    rather than lorem ipsum that would pass any parser.

Regenerate with `python scripts/build_dce_fixture.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures" / "notices" / "replay_48h.json"
OUT = ROOT / "fixtures" / "dce" / "sample_rc.pdf"

# Administrative content a règlement de consultation states, phrased the way one does. These are
# the facts `extract_admin` must find, and the file that pairs with this PDF records them.
ADMIN_SECTIONS = [
    ("Article 1 — Objet de la consultation", None),
    (
        "Article 2 — Procédure",
        "La présente consultation est passée selon la procédure adaptée ouverte en application "
        "des articles L. 2123-1 et R. 2123-1 du code de la commande publique.",
    ),
    (
        "Article 3 — Remise des offres",
        "Les offres devront être remises au plus tard le 14/03/2026 à 12h00 (heure de Paris), "
        "par voie dématérialisée sur le profil d'acheteur https://marches.example-hospital.fr.",
    ),
    (
        "Article 4 — Questions",
        "Toute demande de renseignement devra parvenir au plus tard le 28/02/2026 à 17h00 via la "
        "messagerie du profil d'acheteur.",
    ),
    (
        "Article 5 — Visite des lieux",
        "Une visite sur site est obligatoire. Elle se tiendra le 20/02/2026 à 09h30. "
        "Une attestation de visite sera remise aux candidats.",
    ),
    (
        "Article 6 — Allotissement",
        "La consultation est divisée en deux lots : Lot 1 — Infogérance du système d'information ; "
        "Lot 2 — Maintenance des équipements réseau.",
    ),
    (
        "Article 7 — Jugement des offres",
        "Les offres seront jugées selon les critères pondérés suivants : valeur technique 60 %, "
        "prix des prestations 30 %, performance en matière de développement durable 10 %.",
    ),
    (
        "Article 8 — Variantes",
        "Les variantes ne sont pas autorisées.",
    ),
]


def real_paragraphs(limit: int = 40) -> list[str]:
    """Descriptions from the captured corpus - real notices, real French, real length."""
    notices = json.loads(CORPUS.read_text(encoding="utf-8"))
    if isinstance(notices, dict):
        notices = notices.get("notices", [])
    out: list[str] = []
    for notice in notices:
        for field in ("description", "title"):
            value = (notice.get(field) or "").strip()
            if len(value) > 120:
                out.append(value)
        if len(out) >= limit:
            break
    return out[:limit]


def build() -> None:
    styles = getSampleStyleSheet()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        title="Règlement de consultation (fixture)",
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    body = real_paragraphs()
    story = [Paragraph("Règlement de la consultation", styles["Title"]), Spacer(1, 12)]

    for index, (heading, text) in enumerate(ADMIN_SECTIONS):
        story.append(Paragraph(heading, styles["Heading2"]))
        if text:
            story.append(Paragraph(text, styles["BodyText"]))
        # Real corpus prose between the articles, so pages are realistically dense and the
        # administrative facts are not the only thing on the page.
        for paragraph in body[index * 2 : index * 2 + 2]:
            story.append(Paragraph(paragraph.replace("&", "&amp;").replace("<", "&lt;"), styles["BodyText"]))
        story.append(Spacer(1, 8))
        if index % 2 == 1:
            story.append(PageBreak())

    doc.build(story)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
