# BidPilot — working agreement

> BidPilot finds every public tender a company can actually win — including the ones buried
> on obscure portals — tells it whether to bid, proves that every requirement has evidence
> behind it, and only then drafts the response.

## Read these first

| Document | What it is |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | **The single source of truth.** Product & technical specification, organised F1–F10 with a phased build roadmap (§21). |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | ADR log. **Every deviation from the spec must be recorded here with a reason.** If the code and the spec disagree and there is no ADR, the code is wrong. |

Load §0–§5 of the spec for context, then only the sections relevant to what you are
building. Annexes are reference material.

## The four product principles that bind every decision

These are not aspirations; they decide code reviews.

1. **P1 — Never hallucinate a requirement or a proof.** Every AI-extracted fact carries a
   citation (document, page). Every generated claim carries an evidence reference or a
   visible `[TO PROVIDE]` flag. In the ingestion layer this is the same rule stated as
   *absent means absent*: an adapter that cannot find a value leaves it `None`. It never
   guesses, never defaults, never back-fills.
2. **P2 — Decision before production.** Go/No-Go precedes the response studio.
3. **P3 — Deadlines are sacred.** Any screen showing a tender shows its countdown. A missed
   deadline alert is the worst failure this product can have.
4. **P4 — Explainability.** Every score exposes its breakdown. No opaque numbers.
5. **P5 — Time-to-value < 5 minutes.** SIRET in → live matching tenders out.
6. **P6 — Human in the loop.** AI outputs are drafts with review states, never
   auto-submitted anywhere.

## Definition of Done (applies to every task, §0)

Typed code · unit tests for logic · migration + seed updated · lint clean · feature
demoable from seed data · i18n keys extracted · no secret in code.

## Layout

```
apps/web             Next.js 15 (App Router) — the product UI
apps/api             NestJS REST + OpenAPI — authz, orgs, tenders, matches, billing
packages/db          Prisma schema, migrations, RLS policies, seed  ← single migration authority
packages/shared      Canonical JSON schemas + shared TypeScript types
services/ingestion   Python: source adapters, normalization, dedupe, matching (F1/F3)
fixtures/            Real captured source payloads + the 48h replay corpus
scripts/             Fixture capture and corpus building
docs/                SPEC.md, DECISIONS.md
```

## Conventions

- **Language.** Code, comments and commit messages in English. UI copy is French-first with
  English translations, always through i18n keys — never a hardcoded string.
- **Domain vocabulary.** English domain terms in code (`tender`, `notice`, `buyer`,
  `requirement`, `evidence`); French terms preserved as string literals where they are
  proper nouns (`"mémoire technique"`, `"DC1"`, `"CCTP"`). Never translate CPV/NUTS/DUME.
- **Time.** Store UTC, display in the org's timezone, and warn on the buyer's timezone for
  deadlines. A submission deadline is defined by the buyer's clock (§5).
- **Ids.** ULIDs with a type prefix (`ntc_`, `org_`, `tsk_`).
- **`[VERIFY]` markers in the spec** are facts that move (API field names, thresholds,
  prices). Check them against the live source when you implement, put the confirmed value in
  config or seed data, and record what you found in `docs/DECISIONS.md` — never hardcode.

## Things that will bite you

- **Never bypass `withOrgContext`.** All org-scoped data access goes through it; Postgres
  RLS does the enforcement. It sets `app.org_id` *transaction-locally* because Prisma pools
  connections — a session-level `SET` would leak one tenant's context into the next request.
  With no context set, queries return nothing: the safe default is "see nothing".
- **Migrations are Prisma's job alone.** Python treats the schema as given. Hand-written SQL
  (RLS policies, generated columns, triggers) lives in its own migration alongside the
  generated one.
- **The application role cannot write market data.** Notices, sources and awards are the
  commons; only admin tooling (with `DATABASE_URL`) writes them. That is by design.
- **Adapters refuse to run without a recorded legal basis** (§24.7). Fill the `legal` field
  on the source row.
- **Fixtures are captured, never authored.** See ADR-0001. If a source's shape changed, the
  `unmapped_fields` assertion in the adapter tests is what tells you.
- **No model call outside the LLM gateway** (§17.4) once it exists — that is how cost,
  quality and swap-ability stay controlled.
- **Platform (cross-org) jobs must iterate orgs and set context per org.** `app_all_org_ids()`
  is the only function allowed to see across tenants, and it returns ids and nothing else. Do
  not widen it, and do not give a worker's login role BYPASSRLS (ADR-0011).
- **`DATABASE_URL`'s role must not be a superuser** (ADR-0014). SUPERUSER and BYPASSRLS are both
  exempt from RLS — `FORCE ROW LEVEL SECURITY` does not reach them — so under either, tenant
  context is silently ignored while everything still reports success. The trap is that this is
  the *default*: `initdb --username="$POSTGRES_USER"` makes that role a superuser, so compose and
  CI both drop it (`packages/db/sql/drop_superuser.sql`) and the ingestion service refuses to set
  tenant context on a role that bypasses policies. If a fresh database makes the worker abort
  with `RlsBypassError`, that is this, and the fix is the role, never the guard.
- **A job kind with no handler fails loudly**, on purpose. Do not add a stage to the scheduler
  before its handler exists: a queue that looks healthy while nothing happens is worse than a
  visible dead-letter.

## Commands

```bash
docker compose up -d          # postgres (+pgvector), redis, minio, mailpit
pnpm install
pnpm db:migrate               # apply migrations  (compose runs the privileged bootstrap for you)
pnpm db:seed                  # demo orgs + the real 48h notice corpus
pnpm dev                      # web + api
pnpm worker                   # ingestion worker + scheduler (claims jobs, runs the pipeline)
pnpm worker:once              # drain whatever is runnable, then exit
pnpm test                     # TypeScript tests (includes the cross-tenant RLS suite)
pnpm test:py                  # Python tests (adapters, dedupe, matching)
pnpm lint && pnpm typecheck

python scripts/capture_fixtures.py      # refresh raw source snapshots
python scripts/build_replay_corpus.py   # rebuild the 48h matching corpus
```

## Build order

Follow §21. **Do not start Phase N+1 before Phase N's acceptance criteria pass.** Current
state and what is verified is tracked in [`docs/STATUS.md`](docs/STATUS.md).
