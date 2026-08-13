# `packages/ai` — the prompt registry

Prompt templates versioned in-repo, per SPEC §17.4 and Annex E.1. Each prompt is a directory:

```
prompts/<prompt_id>/<version>/
  system.md     the system prompt
  schema.json   the JSON schema its output must satisfy
  prompt.json   model tier, max tokens, temperature, description
```

## Why the runtime is not here

§17.4 names `packages/ai`, and the *registry* is here — but the gateway that reads it lives in
`services/ingestion/bidpilot_ingestion/ai/`, because every caller does. Extraction, red-flag
detection and generation are all queue jobs (`docs.extract`, `analysis.requirements`,
`gen.section`), and the queue consumer is the Python worker. A TypeScript gateway would mean
either a second network hop for every model call or a second implementation of retry, caching,
accounting and the breaker — and the failure mode of two subtly different gateways is a cost
control that holds in one of them. Same reasoning as ADR-0013, recorded as ADR-0017.

Prompts stay here, as data, so there is one registry rather than one per language: the eval
harness, the worker, and any future TypeScript caller read these same files.

## Rules a prompt must satisfy (Annex E.2)

Checked mechanically by `python scripts/check_prompts.py`, which CI runs:

1. Extraction prompts demand **verbatim quotes and page numbers**, and explicitly permit
   "not found" — a prompt that forces an answer will invent one.
2. Every schema object carrying `quote`/`page` must **require** them. P1: no citation, no fact.
3. Extraction runs at **temperature 0**, or the eval measures a coin flip.
4. Eliminatory detection runs twice with different framings
   (`extract_requirements` + `extract_requirements_adversarial`); the union is taken and
   disagreements are flagged, never silently resolved.

## Changing a prompt

Add a new version directory rather than editing one in place. The gateway's cache is keyed on
the prompt's version, so a new version invalidates it; editing in place would serve answers the
old text produced. A prompt change requires an eval run (E.2 rule 5) — `pnpm eval`.
