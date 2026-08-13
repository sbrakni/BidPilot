"""`pnpm eval` — run the extraction eval and enforce the Annex E.4 gates.

Exit codes are the interface, because CI reads them:

  0  every gate passed
  1  a gate failed, or a case errored
  2  the corpus or a provider key is missing, so nothing was measured

2 is separate from 1 on purpose. "We did not measure" and "we measured and it is bad" are
different facts, and collapsing them is how a pipeline ends up green because the corpus quietly
disappeared.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

from bidpilot_ingestion.documents import ExtractedDocument, page_window, to_prompt_text  # noqa: E402
from bidpilot_ingestion.evals import CorpusMissing, EvalReport, run_eval  # noqa: E402

DEFAULT_CORPUS = ROOT / "fixtures" / "dce"
DEFAULT_REPORT = ROOT / "eval-report.json"

#: Characters of document text per model call. Well inside a large context window: the limit
#: that matters is attention over a 200-page file, not the window, and a register extracted from
#: forty pages at a time is measurably more complete than one extracted from the whole document.
WINDOW_CHARS = 60_000


def build_extractor(cassette: Path | None):
    """The function `run_eval` calls per document.

    Uses the gateway, never a provider SDK directly (§17.4) - including here, so that what the
    eval measures is the same path production takes, prompts, retries, cache and all.
    """
    from bidpilot_ingestion.ai import Gateway, RecordedTransport, load_prompt
    from bidpilot_ingestion.ai.transports import AnthropicTransport

    transport = RecordedTransport(cassette) if cassette is not None else AnthropicTransport()

    gateway = Gateway(transport=transport)
    admin_prompt = load_prompt("extract_admin")
    requirements_prompt = load_prompt("extract_requirements")
    adversarial_prompt = load_prompt("extract_requirements_adversarial")

    def extract(document: ExtractedDocument, case_name: str) -> dict:
        windows = page_window(document, max_chars=WINDOW_CHARS)

        # Admin facts come from the front of the document, where the RC states them.
        admin = gateway.complete(admin_prompt, to_prompt_text(document, pages=windows[0])).data

        requirements: list[dict] = []
        for window in windows:
            text = to_prompt_text(document, pages=window)
            requirements.extend(gateway.complete(requirements_prompt, text).data["requirements"])
            # Annex E.2 rule 2: eliminatory detection runs twice with different framings, and the
            # union is taken. The second pass reads for the rejection rather than for the list.
            requirements.extend(gateway.complete(adversarial_prompt, text).data["requirements"])

        return {"admin": admin, "requirements": _dedupe(requirements)}

    return extract


def _dedupe(requirements: list[dict]) -> list[dict]:
    """Union of the two passes, keeping the stronger claim where they overlap.

    "Stronger" means eliminatory: when the two framings disagree about a requirement's type, the
    one that says "this can end the bid" is the one a user needs to see. Annex E.2 asks for
    disagreements to be flagged, so the survivor records that it was disputed rather than
    silently absorbing the other.
    """
    from bidpilot_ingestion.evals import MATCH_THRESHOLD, similarity

    kept: list[dict] = []
    for candidate in requirements:
        for existing in kept:
            if similarity(candidate.get("text", ""), existing.get("text", "")) >= MATCH_THRESHOLD:
                if candidate.get("type") == "eliminatory" and existing.get("type") != "eliminatory":
                    existing["disputed_type"] = existing.get("type")
                    existing["type"] = "eliminatory"
                elif candidate.get("type") != existing.get("type"):
                    existing["disputed_type"] = candidate.get("type")
                break
        else:
            kept.append(dict(candidate))

    for index, requirement in enumerate(kept, start=1):
        requirement["ref"] = f"REQ-{index:03d}"
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the extraction eval (Annex E.4)")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--cassette",
        type=Path,
        default=None,
        help="replay recorded model responses instead of calling a provider",
    )
    args = parser.parse_args()

    if args.cassette is None and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "eval: no ANTHROPIC_API_KEY and no --cassette, so nothing can be measured.\n"
            "      This is not a pass. Provide a key, or record a cassette.",
            file=sys.stderr,
        )
        return 2

    try:
        report: EvalReport = run_eval(args.corpus, build_extractor(args.cassette))
    except CorpusMissing as exc:
        print(f"eval: {exc}", file=sys.stderr)
        print("      An eval with no corpus must fail, never report success.", file=sys.stderr)
        return 2

    args.report.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")

    print(f"\nExtraction eval — {len(report.cases)} case(s), Annex E.4 gates:")
    for line in report.summary_lines():
        print(line)

    for case in report.cases:
        if case.error:
            print(f"\n  ERROR {case.case}: {case.error}")
        if case.missed_eliminatory:
            print(f"\n  {case.case} missed eliminatory requirements:")
            for ref in case.missed_eliminatory:
                print(f"    - {ref}")

    print(f"\nreport: {args.report}")
    if not report.passed:
        print("\nFAILED: a gate is below its threshold. Per Annex E.2 rule 5, this blocks merge.")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
