# Build status

Tracked against the phased roadmap in [`SPEC.md`](SPEC.md) §21. The rule from §0 applies:
**do not start Phase N+1 before Phase N's acceptance criteria pass.**

Last updated: 2026-08-12.

---

## Summary

| Phase | State |
|---|---|
| **Phase 0 — Foundations** | Substantially complete; **web app shell not built** (see gaps) |
| **Phase 1 — "Radar"** | Ingestion core and matching engine built and verified; job runner, digests and UI outstanding |
| Phase 2 — "Decision" | Not started |
| Phase 3 — "Studio" | Not started |
| Phase 4 — "Coverage & intelligence" | Not started |
| Phase 5 — "Expansion" | Not started |

Verified means: it runs, and a test asserts the behaviour. Everything below marked ✅ was
exercised against a real Postgres and, for the adapters, against live source payloads.

---

## What is verified

### Sourcing engine — F1 (§6)

| Item | State | Evidence |
|---|---|---|
| Canonical notice contract (Annex B.1) | ✅ | JSON Schema + pydantic + Zod, cross-validated on the real corpus |
| Connector SDK (§6.2) | ✅ | legal basis required to construct an adapter; jittered backoff, rate limiting, order-independent content hashing — all tested |
| TED adapter (§6.3) | ✅ | normalizes every fixture notice; date/title/language handling unit-tested |
| BOAMP adapter (§6.3) | ✅ | eForms + legacy shapes, HTML entity decoding, département→NUTS bridge |
| Deduplication & clustering (§6.7) | ✅ | all three resolution keys, re-entrant, merge policy; **29 genuine multi-source clusters found in live data**, including TED↔BOAMP pairs |
| Amendment detection groundwork | ⚠️ partial | content hashing and `notice_versions` exist; the re-fetch → diff → alert loop is not wired |
| Source registry as data (§6.1) | ✅ | 10 seeded sources; Tier-2 rows disabled pending legal review, per §24.7 |
| Email connector (§6.5) | ❌ | source row seeded, inbound parsing not implemented |
| Source health monitoring (§6.1) | ❌ | columns exist; no freshness SLO job or alerting |

### Matching — F3 (§8)

| Item | State | Evidence |
|---|---|---|
| Stage-1 hard filters | ✅ | each veto independently tested with its own reason |
| Stage-2 explainable score | ✅ | factors sum exactly to the score, asserted over 200 real notices |
| Stage-3 cost guard | ✅ | a stage-1 failure is never scored — structural, not a check |
| **§8.4 acceptance: ≥10 matches for a seeded IT/IDF org** | ✅ | **14 matches** from the 1,421-notice 48h replay, zero filter violations |
| Three sector packs produce matches | ✅ | IT / training / maintenance, all from real data |
| Inbox states + dismissal reasons (§8.3) | ✅ | API-level, with reasons recorded for weight tuning |
| Daily digest, instant alerts | ❌ | thresholds defined; no notifier |

### Data model & tenancy — §17.6, §18, F10

| Item | State | Evidence |
|---|---|---|
| Full core schema (§18) | ✅ | migrations apply from scratch |
| **Cross-tenant isolation (§15.5)** | ✅ | 17 tests: reads, writes, aggregates, relation traversal, raw SQL, fail-closed default |
| Market data read-only for tenants | ✅ | tested; the seeder connects as owner (ADR-0007) |
| Append-only decision log (§10.6) | ✅ | trigger-enforced, with an audited erasure path for GDPR (ADR-0009) |
| FTS + trigram + HNSW indexes (§17.5) | ✅ | applied; `vector` not `halfvec` (ADR-0004) |
| Seed creates a full demo org | ✅ | 3 persona orgs, real notices, computed matches; idempotent |
| Auth.js sessions (§17.1) | ❌ | the API uses a dev header, refused when `NODE_ENV=production` |
| Stripe billing (§15.2) | ❌ | schema only |

### API — §19

| Item | State |
|---|---|
| `/health`, `/health/ready` | ✅ version-neutral |
| `/v1/org`, `/v1/matches` (+ shortlist/dismiss/pursue), `/v1/notices/{id}` | ✅ 15 API tests |
| OpenAPI document at `/docs` | ✅ generated |
| Everything else in §19 | ❌ |

### Quality gates — §16

| Gate | State |
|---|---|
| Typecheck, build, lint (TS) | ✅ |
| ruff check + format (Python) | ✅ |
| Unit + integration tests | ✅ 74 Python, 42 TypeScript |
| CI running all of the above | ✅ `.github/workflows/ci.yml` |
| E2E happy paths (Playwright) | ❌ needs the web app |
| **AI eval harness (Annex E.4)** | ❌ **placeholder job in CI**; must become blocking before any extraction prompt ships |

---

## Known gaps, in the order they should be closed

1. **Web app (`apps/web`) is not built.** This is the largest Phase 0 gap: §21 Phase 0 exit
   requires `pnpm dev` to boot the UI and a demo user to see empty states. The API and
   database are ready for it, and §20.2 gives the screen build order (onboarding wizard →
   match inbox → tender workspace). Nothing else here is blocked by it.
2. **Job runner and scheduler.** The `jobs` table and the job kinds in §17.3 exist as
   schema; no worker claims them yet. Until then ingestion runs only when invoked directly,
   so the §6.9 freshness SLO is not being met by construction.
3. **Auth.js** — replaces the dev header. The tenant-resolution seam is already in place.
4. **Source health monitoring** (§6.1). The spec is explicit that this is not optional: "an
   aggregator that silently loses a source is lying to its customers."
5. **Email connector** (§6.5), which §21 requires to ship in Phase 1.
6. Then Phase 2: the document pipeline, extractions with citations, and the eval harness
   with its gates enforced in CI.

## Deliberate scope choices

- Fixtures are captured from live APIs, never authored (ADR-0001).
- Deviations from the spec are recorded in [`DECISIONS.md`](DECISIONS.md); there are nine so
  far, four of them forced by facts the spec marked `[VERIFY]`.
- Tier-2 scraping sources are seeded **disabled**, because §24.7 requires a per-source legal
  review before they run and the adapter refuses to start without one.
- No LLM code has been written yet. §17.4 requires all model access to go through the
  gateway package, so the first model call should arrive with that gateway, not before it.
