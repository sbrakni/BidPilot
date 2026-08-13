"""Check every prompt in the registry against Annex E.1 and E.2.

A prompt is behaviour. These rules are the ones the product's honesty rests on - verbatim
quotes, page anchors, permission to answer "not found" - and a prompt that quietly loses one
would still run, still return well-formed JSON, and start inventing facts.

So they are checked mechanically, on every push, rather than at review time. Run by the `ai-eval`
CI job; also useful before writing a new prompt version.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

from bidpilot_ingestion.ai.registry import PROMPTS_ROOT, available_prompts, load_prompt  # noqa: E402

#: E.2 rule 1, as things a prompt's text must actually say. Matched loosely - the rule is that
#: the instruction is present, not that it is phrased our way.
QUOTE_MARKERS = ("verbatim", "copied", "copy")
NOT_FOUND_MARKERS = ("not found", "null", "do not infer", "n'a pas", "absent")

#: Extraction prompts must anchor to pages; generation prompts will have their own rule (E.2
#: rule 3) when they arrive.
EXTRACTION_PREFIX = "extract_"


def check(prompt_id: str) -> list[str]:
    problems: list[str] = []
    try:
        prompt = load_prompt(prompt_id)
    except Exception as exc:
        return [f"{prompt_id}: will not load ({exc})"]

    system = prompt.system.lower()

    if prompt.temperature != 0.0 and prompt_id.startswith(EXTRACTION_PREFIX):
        problems.append(
            f"{prompt.ref}: temperature is {prompt.temperature}, but extraction must be "
            "reproducible or the eval measures a coin flip"
        )

    if prompt.schema.get("type") != "object":
        problems.append(f"{prompt.ref}: top-level schema must be an object")

    if prompt_id.startswith(EXTRACTION_PREFIX):
        if not any(marker in system for marker in QUOTE_MARKERS):
            problems.append(
                f"{prompt.ref}: E.2 rule 1 requires demanding verbatim quotes; the system prompt "
                "never says so"
            )
        if "page" not in system:
            problems.append(f"{prompt.ref}: E.2 rule 1 requires page numbers; none are demanded")
        if not any(marker in system for marker in NOT_FOUND_MARKERS):
            problems.append(
                f'{prompt.ref}: E.2 rule 1 requires explicitly allowing "not found"; a prompt '
                "that forces an answer will invent one"
            )
        problems.extend(_check_citation_fields(prompt))

    return problems


def _check_citation_fields(prompt) -> list[str]:
    """Every extracted item must be *required* to carry page and quote.

    Enforced in the schema rather than only in the prose, because the schema is what the gateway
    validates: an optional citation is a citation the model may omit under pressure, which is
    exactly when it is most needed.
    """
    problems: list[str] = []

    def walk(node, path: str) -> None:
        if not isinstance(node, dict):
            return
        properties = node.get("properties", {})
        if {"quote", "page"} & set(properties):
            required = set(node.get("required", []))
            missing = {"quote", "page"} - required
            if missing:
                problems.append(
                    f"{prompt.ref}: {path} has {sorted({'quote', 'page'} & set(properties))} but "
                    f"does not require {sorted(missing)} - P1 means no citation, no fact"
                )
        for name, child in properties.items():
            walk(child, f"{path}.{name}")
        if "items" in node:
            walk(node["items"], f"{path}[]")

    walk(prompt.schema, "$")
    return problems


def main() -> int:
    prompts = available_prompts()
    if not prompts:
        print(f"no prompts found in {PROMPTS_ROOT}", file=sys.stderr)
        return 1

    problems: list[str] = []
    for prompt_id in prompts:
        problems.extend(check(prompt_id))

    for prompt_id in prompts:
        print(f"  checked {prompt_id}")

    if problems:
        print("\nAnnex E.2 violations:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"\n{len(prompts)} prompt(s) satisfy Annex E.1/E.2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
