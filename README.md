# BidPilot

> Finds every public tender a company can actually win — including the ones buried on
> obscure portals — tells it whether to bid, proves that every requirement has evidence
> behind it, and only then drafts the response.

A decision-first bid platform for SMEs in France, Belgium and Luxembourg. The full product
and technical specification is [`docs/SPEC.md`](docs/SPEC.md); build state is
[`docs/STATUS.md`](docs/STATUS.md); every deviation from the spec is recorded in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

**Current state:** the sourcing engine, matching engine, data model and API are built and
tested. The web UI is not yet built — see [`docs/STATUS.md`](docs/STATUS.md).

## Why it is built in this order

The spec's thesis (§1.3) is that LLM text generation is a commodity and the defensible value
is upstream of it, so the build follows that order:

1. **Exhaustive sourcing** — aggregate every relevant source, including the long tail. This
   compounds and is operationally hard, and it is what makes everything downstream
   trustworthy: *if it's not in BidPilot, it doesn't exist.*
2. **The bid/no-bid decision** — an explainable eligibility verdict. Saying "NO-GO, and
   here's why, in 90 seconds" saves more money than any generated paragraph.
3. **Evidence-first compliance** — every requirement links to a proof or is flagged as a gap.
4. *Only then*, assisted production.

## Quick start

Requires Node 22+, pnpm 10+, Python 3.11+, and Docker (or a local Postgres 16 with
`pgvector`, `pg_trgm` and `unaccent`).

```bash
cp .env.example .env
docker compose up -d                 # postgres + redis + minio + mailpit
pnpm install

# One-time: create the non-owner application role that RLS applies to (ADR-0005).
psql "$DATABASE_URL" -v app_password=bidpilot_app -f packages/db/sql/bootstrap_roles.sql

pnpm db:generate && pnpm db:migrate
pnpm db:seed                         # 3 demo orgs + a real 48h notice corpus
pnpm dev                             # api on :3001 (OpenAPI at /docs)
```

Try the seeded match inbox — real tenders, computed scores:

```bash
curl -s -H 'x-bidpilot-user: user_lea' 'localhost:3001/v1/matches?limit=3' | jq
```

`x-bidpilot-user` is a development shortcut standing in for Auth.js sessions. It is refused
when `NODE_ENV=production`.

## Tests

```bash
pnpm test        # TypeScript: cross-tenant RLS suite + API suite (needs Postgres)
pnpm test:py     # Python: adapters, dedupe, matching
pnpm lint && pnpm typecheck
```

Two suites matter more than the others:

- **`packages/db/src/rls.test.ts`** proves tenant isolation against a real Postgres — reads,
  writes, aggregates, relation traversals and raw SQL, plus the fail-closed default.
- **`services/ingestion/tests/`** runs against payloads captured from the live TED and BOAMP
  APIs, so a source changing shape fails a test instead of silently degrading the feed.

## Fixtures

```bash
python scripts/capture_fixtures.py       # refresh raw per-source snapshots
python scripts/build_replay_corpus.py    # rebuild the 48h matching corpus
```

Fixtures are captured, never hand-written (ADR-0001) — invented payloads test our
imagination rather than the sources. Refreshing them is a reviewable event: read the diff,
and treat an unexpected change as a source-shape alert.

## Layout

```
apps/web             Next.js 15 — the product UI (not yet built)
apps/api             NestJS REST + OpenAPI
packages/db          Prisma schema, migrations, RLS policies, seed  ← single migration authority
packages/shared      Canonical schemas (JSON Schema + Zod) and shared types
services/ingestion   Python: source adapters, normalization, dedupe, matching
fixtures/            Real captured payloads + the 48h replay corpus
scripts/             Fixture capture and corpus building
docs/                SPEC.md · STATUS.md · DECISIONS.md
```

Contributors: read [`CLAUDE.md`](CLAUDE.md) first — it covers the conventions and the few
things that will bite you (chiefly: never bypass `withOrgContext`).
