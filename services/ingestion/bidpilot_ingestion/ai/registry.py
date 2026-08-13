"""Model tiers and the prompt registry (SPEC §17.4, Annex E.1).

Two registries, both deliberately *data* rather than code:

  * **Tiers.** Call sites ask for S, M or L - "cheap", "normal", "hard" - never for a model id.
    Model names change every few months; a codebase that names them at call sites has to be
    edited to swap one, which is exactly the swap-ability §17.4 exists to protect. The mapping
    lives in environment config, so switching tier M is a deployment change.
  * **Prompts.** Each prompt is a versioned directory holding its system text, its JSON schema,
    its tier and its sampling settings. Versioned because Annex E.2 makes a prompt change a
    thing CI must re-evaluate: a prompt is behaviour, and behaviour that can change silently
    cannot be gated.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

ModelTier = Literal["S", "M", "L"]

#: Where the prompt registry lives (Annex E.1). Outside the Python package on purpose: the eval
#: harness and any future TypeScript caller read the same files, so there is one registry rather
#: than one per language.
PROMPTS_ROOT = Path(__file__).resolve().parents[4] / "packages" / "ai" / "prompts"

_TIER_ENV = {"S": "LLM_MODEL_TIER_S", "M": "LLM_MODEL_TIER_M", "L": "LLM_MODEL_TIER_L"}

#: Used when a tier has no configured model. Not a silent default: `resolve_model` raises
#: instead, because quietly falling back to a cheaper model would change extraction quality
#: without anything in the output saying so.
_UNSET = object()


class PromptNotFound(LookupError):
    """A prompt id or version that the registry does not contain."""


def resolve_model(tier: ModelTier) -> str:
    """The concrete model id for a tier.

    Raises rather than defaulting. A missing configuration is an operator error, and inventing a
    model here would mean an analysis silently ran on a different one than the eval gates
    measured (Annex E.4).
    """
    key = _TIER_ENV.get(tier)
    if key is None:
        raise ValueError(f"unknown model tier {tier!r}; expected one of S, M, L")
    model = os.environ.get(key)
    if not model:
        raise RuntimeError(
            f"{key} is not set, so tier {tier} has no model. Set it in the environment "
            "(see .env.example); the gateway will not guess one."
        )
    return model


@dataclass(frozen=True)
class PromptSpec:
    """One versioned prompt: its text, its contract, and how to run it."""

    id: str
    version: str
    system: str
    schema: dict[str, Any]
    tier: ModelTier
    max_tokens: int
    temperature: float
    description: str

    @property
    def ref(self) -> str:
        """`extract_admin@v1`, the form Annex E.1 uses and the form recorded on every result."""
        return f"{self.id}@{self.version}"


def _prompt_dir(prompt_id: str, version: str) -> Path:
    return PROMPTS_ROOT / prompt_id / version


@lru_cache(maxsize=64)
def load_prompt(prompt_id: str, version: str = "latest") -> PromptSpec:
    """Load a prompt from the registry.

    `latest` resolves to the highest version directory present, which is what development uses;
    production paths should name a version, so that a new prompt does not take effect until it
    has been through the eval gates (Annex E.2 rule 5).
    """
    root = PROMPTS_ROOT / prompt_id
    if not root.is_dir():
        raise PromptNotFound(f"no prompt {prompt_id!r} in {PROMPTS_ROOT}")

    if version == "latest":
        versions = sorted(
            (path.name for path in root.iterdir() if path.is_dir() and path.name.startswith("v")),
            key=lambda name: int(name[1:]) if name[1:].isdigit() else -1,
        )
        if not versions:
            raise PromptNotFound(f"prompt {prompt_id!r} has no versions")
        version = versions[-1]

    directory = _prompt_dir(prompt_id, version)
    if not directory.is_dir():
        raise PromptNotFound(f"no version {version!r} of prompt {prompt_id!r}")

    config = json.loads((directory / "prompt.json").read_text(encoding="utf-8"))
    system = (directory / "system.md").read_text(encoding="utf-8").strip()
    schema = json.loads((directory / "schema.json").read_text(encoding="utf-8"))

    return PromptSpec(
        id=prompt_id,
        version=version,
        system=system,
        schema=schema,
        tier=config["tier"],
        max_tokens=int(config["max_tokens"]),
        # Extraction runs at 0: the same document must produce the same register twice, or the
        # eval numbers describe a coin flip rather than the prompt.
        temperature=float(config.get("temperature", 0.0)),
        description=config.get("description", ""),
    )


def available_prompts() -> list[str]:
    """Every prompt id in the registry, for the eval harness and for operator tooling."""
    if not PROMPTS_ROOT.is_dir():
        return []
    return sorted(path.name for path in PROMPTS_ROOT.iterdir() if path.is_dir())
