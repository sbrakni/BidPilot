# Build status

Tracked against the phased roadmap in [`SPEC.md`](SPEC.md) §21. The rule from §0 applies:
**do not start Phase N+1 before Phase N's acceptance criteria pass.**

Last updated: 2026-08-13.

---

## Summary

| Phase | State |
|---|---|
| **Phase 0 — Foundations** | Complete except Auth.js (a demo session picker stands in) |
| **Phase 1 — "Radar"** | Ingestion runs end-to-end on a schedule; deadline alerts and coverage status live. Email connector, digest email and onboarding wizard outstanding |
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
| Amendment detection | ✅ | a changed content hash creates a version with a diff, flags the notice `amended`, and enqueues the team alert - tested end-to-end |
| Pipeline stages wired to the database | ✅ | `fetch → store raw → normalize → dedupe → match`, each a separate restartable job; run live against TED (25 notices → 75 jobs, 0 dead) |
| Job queue (§17.1, §17.3) | ✅ | Postgres-backed, `FOR UPDATE SKIP LOCKED`, idempotency keys, capped retries, dead-letter, stale-claim recovery |
| Scheduler (§17.1) | ✅ | cron per source row plus platform jobs; idempotent per time bucket, so redundant schedulers are safe (§16) |
| Source registry as data (§6.1) | ✅ | 10 seeded sources; Tier-2 rows disabled pending legal review, per §24.7 |
| Email connector (§6.5) | ❌ | source row seeded, inbound parsing not implemented |
| Source health monitoring (§6.1) | ✅ | per-tier silence budgets and the week-over-week yield-drop check, with transitions recorded as events |
| Public coverage status (§6.9 MUST) | ✅ | `GET /v1/status/coverage`, unauthenticated, declaring the JAL gap explicitly |

### Matching — F3 (§8)

| Item | State | Evidence |
|---|---|---|
| Stage-1 hard filters | ✅ | each veto independently tested with its own reason |
| Stage-2 explainable score | ✅ | factors sum exactly to the score, asserted over 200 real notices |
| Stage-3 cost guard | ✅ | a stage-1 failure is never scored — structural, not a check |
| **§8.4 acceptance: ≥10 matches for a seeded IT/IDF org** | ✅ | **14 matches** from the 1,421-notice 48h replay, zero filter violations |
| Three sector packs produce matches | ✅ | IT / training / maintenance, all from real data |
| Inbox states + dismissal reasons (§8.3) | ✅ | API-level, with reasons recorded for weight tuning |
| Instant high-score alerts | ⚠️ partial | the `notify.send` job is enqueued above the threshold; nothing delivers it yet |
| Daily digest | ❌ | deliberately not scheduled until the notifier exists, so the queue stays clean |

### Data model & tenancy — §17.6, §18, F10

| Item | State | Evidence |
|---|---|---|
| Full core schema (§18) | ✅ | migrations apply from scratch |
| **Cross-tenant isolation (§15.5)** | ✅ | 17 tests: reads, writes, aggregates, relation traversal, raw SQL, fail-closed default |
| Market data read-only for tenants | ✅ | tested; the seeder connects as owner (ADR-0007) |
| Append-only decision log (§10.6) | ✅ | trigger-enforced, with an audited erasure path for GDPR (ADR-0009) |
| FTS + trigram + HNSW indexes (§17.5) | ✅ | applied; `vector` not `halfvec` (ADR-0004) |
| Seed creates a full demo org | ✅ | 3 persona orgs, real notices, computed matches; idempotent |
| Deadline alerts (§12.4, P3) | ✅ | J-14/7/3/1 in **working days**, computed each tick so a moved deadline self-corrects; escalation to Owner after 24h unacknowledged |
| Vault freshness engine (§7.2) | ✅ | expiry recomputed per org rather than trusted, since eligibility reads this status |
| Auth.js sessions (§17.1) | ❌ | a demo persona picker stands in; the API refuses its header when `NODE_ENV=production` |
| Stripe billing (§15.2) | ❌ | schema only |

### Web app — §20

| Item | State |
|---|---|
| App shell with the §20.1 information architecture | ✅ |
| "Aujourd'hui" triage surface (§20.1) | ✅ counters + top matches |
| Match inbox (§20.2 screen 2) | ✅ score ring, factor breakdown, deadline chip, source badges, triage actions |
| Design tokens (§20.3) | ✅ red reserved for eliminatory/deadline danger only |
| i18n, French-first (§20.6) | ✅ FR + EN via next-intl |
| Onboarding wizard (§15.3), tender workspace (§12.1), matrix, studio | ❌ sections render honest empty states rather than 404s |

### API — §19

| Item | State |
|---|---|
| `/health`, `/health/ready` | ✅ version-neutral |
| `/v1/org`, `/v1/matches` (+ shortlist/dismiss/pursue), `/v1/notices/{id}`, `/v1/status/coverage` | ✅ 17 API tests |
| OpenAPI document at `/docs` | ✅ generated |
| Everything else in §19 | ❌ |

### Quality gates — §16

| Gate | State |
|---|---|
| Typecheck, build, lint (TS) | ✅ |
| ruff check + format (Python) | ✅ |
| Unit + integration tests | ✅ 137 Python, 44 TypeScript |
| CI running all of the above | ✅ `.github/workflows/ci.yml` |
| E2E happy paths (Playwright) | ❌ the app is built and manually verified; the automated pass is not written |
| **AI eval harness (Annex E.4)** | ❌ **placeholder job in CI**; must become blocking before any extraction prompt ships |

---

## Known gaps, in the order they should be closed

1. **Notification delivery.** Deadline alerts, amendment alerts and high-score pushes all
   *raise* correctly - a `notifications` row plus a `notify.send` job - but nothing sends them
   yet. This is the highest-priority gap because P3 makes a missed deadline the worst failure
   the product has, and an alert that is computed but never delivered is not an alert. Needs
   the TS notifier of §17.2 plus the daily digest (§8.3).
2. **Auth.js** (§17.1), replacing the demo persona picker. The tenant-resolution seam already
   exists and is tested, so this is contained.
3. **Email inbox connector** (§6.5), which §21 requires in Phase 1. It is the universal
   fallback that "covers" any portal able to send an alert mail, including authenticated ones.
4. **Onboarding wizard** (§15.3) - the scripted path to the P5 "value in under five minutes"
   promise. The matching it depends on already works.
5. **Remaining Tier-1/2 adapters**: BOAMP is live, PLACE, BOSA (BE) and the atexo family are
   seeded as rows but disabled pending the legal review §24.7 requires.
6. Then Phase 2: the DCE document pipeline, extractions with page-anchored citations, the
   eval harness with its Annex E.4 gates enforced in CI, and the Go/No-Go brief.

## Deliberate scope choices

- Fixtures are captured from live APIs, never authored (ADR-0001).
- Deviations from the spec are recorded in [`DECISIONS.md`](DECISIONS.md); there are twelve so
  far, several forced by facts the spec marked `[VERIFY]` and several by problems that only
  appeared once the code ran against a real database.
- Tier-2 scraping sources are seeded **disabled**, because §24.7 requires a per-source legal
  review before they run and the adapter refuses to start without one.
- No LLM code has been written yet. §17.4 requires all model access to go through the
  gateway package, so the first model call should arrive with that gateway, not before it.
- A job kind with no handler fails loudly rather than reporting success, and `digest.daily` is
  therefore not scheduled yet: a queue that looks healthy while the work never happens is worse
  than a visible dead-letter.
