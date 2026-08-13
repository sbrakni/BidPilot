# Architecture Decision Record log

Every deviation from `docs/SPEC.md` is recorded here with its reason (SPEC §0). If the code
and the spec disagree and there is no ADR, the code is wrong.

Format: one entry per decision, newest last. Status is `accepted`, `superseded` or
`revisit-at-phase-N`.

---

## ADR-0001 — Fixture strategy: capture real payloads, split raw from normalized

**Date:** 2026-08-12 · **Status:** accepted · **Relates to:** §21.1, Annex E.3

**Decision.** Adapter fixtures are *captured from the live APIs* by
`scripts/capture_fixtures.py`, never hand-written. Two tiers:

| Tier | Files | Form | Pins |
|---|---|---|---|
| Per-source snapshots | `fixtures/notices/{ted,boamp}_*.json` | small, pretty-printed, **raw** | adapter behaviour; a source changing shape shows in the diff |
| Replay corpus | `fixtures/notices/replay_48h.json` | ~1,400 notices, compact, **normalized** | matching & clustering at realistic volume |

**Why.** Invented fixtures test our imagination, not the sources. Writing a plausible TED
payload by hand would have missed every one of the shape facts recorded in ADR-0002 — and
those facts are exactly what breaks in production.

**Why the replay corpus is normalized rather than raw.** A genuine 48h FR/BE/LU window is
~1,400 notices whose raw eForms payloads weigh ~15 MB. Carrying that in git for every clone
and CI run is not worth it, and matching consumes canonical notices anyway. The raw
snapshots keep normalization honest, so the corpus can safely be a derived artifact.
Rebuild with `python scripts/build_replay_corpus.py`.

**Consequence.** Refreshing fixtures is a reviewable event: re-run the capture, read the
diff, and treat an unexpected change as a source-shape alert (`unmapped_fields` is asserted
empty in tests).

---

## ADR-0002 — Source API facts verified against live endpoints

**Date:** 2026-08-12 · **Status:** accepted · **Relates to:** §6.3 `[VERIFY]` markers

The spec marked TED field names and the BOAMP dataset schema as `[VERIFY]`. Verified
against the live APIs; the findings changed the adapter design, so they are recorded rather
than left in code comments alone.

**TED (`POST https://api.ted.europa.eu/v3/notices/search`)**

- No API key required for search. Confirms §6.3.
- **No `sort` parameter** — sending one returns 400. Incremental fetch therefore cannot
  "read newest first"; it bounds a publication-date window in the expert query and pages
  through it. The cursor is a date watermark, not an offset.
- `fields` is validated server-side against ~1,830 business terms; one unknown name fails
  the entire request. The verified subset lives in `adapters/ted.py::TED_FIELDS`.
- Multilingual values arrive as ISO-639-3 keyed maps, inconsistently wrapped:
  `notice-title` is `{"fra": "…"}` but `description-lot` is `{"fra": ["…"]}`.
- `publication-date` is a **date with offset** (`2026-08-11+02:00`), not an instant. A lot
  deadline arrives split across `deadline-receipt-tender-date-lot` and `…-time-lot`; the
  offset on the *time* part is the buyer's clock and governs (§5).
- `place-of-performance` mixes NUTS codes with ISO-3166 **alpha-3 country codes** and
  repeats values per lot. `classification-cpv` and `contract-nature` repeat likewise.
- `notice-title` is composed as `"<Country> – <CPV label> – <real title>"`, while
  `title-lot` is often just a buyer's internal reference (`"WS2848982494 - 1"`). Neither is
  reliable alone, so the adapter picks between them (see `_title`).

**BOAMP (Opendatasoft Explore v2.1, dataset `boamp`, licence etalab-2.0)**

- Default order is **oldest first**; `order_by=dateparution desc` is required.
- `donnees` is a **JSON string**, not an object. Recent records contain the full eForms UBL
  tree (`{"EFORMS": {"ContractNotice": …}}`); archive records use a legacy `IDENTITE`
  shape. Both are handled — the archive is what feeds the renewal radar (§14.2).
- Text is HTML-entity encoded (`Communauté d&#039;Agglomération`). Decoded on ingest, once,
  rather than at every render site.
- `titulaire` is a list of bare supplier **names** with no SIREN, sometimes duplicated. The
  winner's SIREN stays null and is resolved later against DECP; guessing it would
  mis-attribute wins on the competitor pages (§14.3).
- Responses above ~100 records are gzipped, and the geography is published as
  **départements**, not NUTS — hence the `dept_to_nuts` bridge, without which every BOAMP
  notice would fail a NUTS hard filter (§8.1).

**Consequence.** Both sources emit eForms, so that parsing lives in one shared
`eforms.py` — the same "one adapter family, many portals" structure §6.4 depends on.

---

## ADR-0003 — Python 3.11 rather than 3.12

**Date:** 2026-08-12 · **Status:** revisit-at-phase-2 · **Relates to:** §17.1

The spec specifies Python 3.12; the build environment ships 3.11. Nothing in the ingestion
service needs 3.12 (`StrEnum` and PEP-604 unions are both 3.11), so `requires-python` is
`>=3.11` and CI runs 3.12 to keep the spec's target verified. Revisit when a 3.12-only
feature is genuinely wanted.

---

## ADR-0004 — `vector(1024)` instead of `halfvec(1024)`

**Date:** 2026-08-12 · **Status:** revisit-at-phase-2 · **Relates to:** §17.5

**Decision.** Embedding columns are `vector(1024)` with HNSW cosine indexes, not the
spec's `halfvec(1024)`.

**Why.** `halfvec` requires pgvector ≥ 0.7. Debian/Ubuntu still package 0.6.0, so a
`halfvec` migration fails on a stock distribution install while succeeding on the
`pgvector/pgvector:pg16` image — a difference between environments in the one place where
schema drift is most expensive to discover.

**Trade-off accepted.** `halfvec` halves index storage (2 bytes/dimension instead of 4).
At the design scale (2M notices × 1024 dims ≈ 8 GB as `vector`, 4 GB as `halfvec`) this is
a real but affordable cost, and it buys environment parity now.

**Migration path.** When the deployment target guarantees pgvector ≥ 0.7:
`ALTER TABLE notices ALTER COLUMN embedding TYPE halfvec(1024)`, then rebuild the HNSW
index. No application code changes, because the column type is opaque to Prisma
(`Unsupported`) and to the Python side.

---

## ADR-0005 — Role creation is a privileged bootstrap step, not a migration

**Date:** 2026-08-12 · **Status:** accepted · **Relates to:** §17.6

**Decision.** `packages/db/sql/bootstrap_roles.sql` creates the `bidpilot_app` login role
and is run once by a superuser. The RLS migration *requires* the role to exist and aborts
with instructions if it does not.

**Why.** `CREATE ROLE` needs the CREATEROLE attribute, which the migration user does not
have on managed Postgres (RDS, Scaleway) and should not have. The tempting alternative —
create-if-possible, skip otherwise — would let a deploy silently apply the schema *without*
the isolation policies. Failing loudly is the only safe behaviour when the failure mode is
a cross-tenant data leak.

**Consequence.** `docker-compose.yml` mounts the bootstrap script into the Postgres init
directory, so local development still needs no manual step.

---

## ADR-0006 — `users` gets per-command RLS policies

**Date:** 2026-08-12 · **Status:** accepted · **Relates to:** §15.1, §17.6

**Decision.** `users` is not org-scoped. Policies are split per command: `SELECT` limited to
co-members of the current org plus yourself, `INSERT` permitted, `UPDATE`/`DELETE`
restricted to your own row via `app.user_id`.

**Why.** The first implementation used one membership-gated policy for all commands, which
made signup impossible: at signup (and at invitation acceptance) the user row must exist
*before* any membership does, so the check could never pass. The threat actually worth
closing is **enumeration** — org A listing org B's people — not row creation. Multi-org
membership (§15.1) is precisely why identity cannot live inside a tenant boundary.

**Consequence.** `withOrgContext` accepts an optional `userId`, and the API sets both after
authentication.

---

## ADR-0007 — Seeding connects as the database owner

**Date:** 2026-08-12 · **Status:** accepted · **Relates to:** §17.6, §18.1

The seed writes shared market data (notices, sources, thresholds), which the application
role is deliberately denied. Rather than widening that grant, the seeder connects with
`DATABASE_URL` (owner) and is treated as admin tooling. The application role's inability to
write the commons is the isolation working as designed, and it is asserted in the RLS suite.

---

## ADR-0010 — Every instant is `timestamptz`

**Date:** 2026-08-13 · **Status:** accepted · **Relates to:** §5, §16, P3

**Decision.** Every `DateTime` field carries `@db.Timestamptz`; no column stores a naive
timestamp.

**Why.** Prisma's default maps `DateTime` to `timestamp without time zone`, which is the wrong
type for this product. A submission deadline is defined by the buyer's clock (§5), and P3 makes
a missed deadline the worst failure there is. A column that discards the offset means any writer
with a non-UTC session silently shifts the value, and Postgres cannot compare it to `now()`
without assuming a zone.

Found by wiring the ingestion pipeline to the database: Python raised "can't compare
offset-naive and offset-aware datetimes" on a deadline comparison. That error was the symptom;
the type was the cause.

**Migration.** `ALTER COLUMN ... TYPE timestamptz USING value AT TIME ZONE 'UTC'` - stating what
the stored values already were, rather than reinterpreting anything.

---

## ADR-0011 — Platform jobs enumerate tenants through one privileged function

**Date:** 2026-08-13 · **Status:** accepted · **Relates to:** §17.6, §12.4

**Decision.** `app_all_org_ids()` is a `SECURITY DEFINER` function owned by
`bidpilot_platform`, a NOLOGIN BYPASSRLS role that owns nothing else. Cross-org background jobs
call it for the list of orgs, then process each one *inside that org's tenant context*.

**Why.** Deadline alerts, vault freshness and digests have to sweep every tenant, but `orgs` is
RLS-protected and the tables use FORCE ROW LEVEL SECURITY - so even the schema owner sees
nothing without context. That fail-closed default is deliberate and worth keeping.

Two alternatives were rejected:

- **BYPASSRLS on the worker's login role.** Ambient and coarse: every query that role makes
  would be unfiltered, so one mistake in a worker leaks across tenants with nothing to catch it.
- **Relaxing the policies so "no context sees everything".** This inverts the fail-closed
  default: an API path that forgot to establish context would suddenly see every tenant. It is
  the smallest diff and by far the most dangerous option.

What makes the chosen approach safe is that the privileged surface is one function returning one
column of ids, and every row the job then reads or writes is still policy-checked. A test
asserts both properties - the definer bypasses RLS and cannot log in, and the function exposes
exactly one output column.

**Note.** The first attempt failed instructively: a SECURITY DEFINER function owned by the
schema owner is still filtered, because FORCE RLS applies to the owner. The BYPASSRLS definer is
required, not decorative.

---

## ADR-0012 — Deadline alerts are computed each tick, never pre-scheduled

**Date:** 2026-08-13 · **Status:** accepted · **Relates to:** §12.4, §16, P3

**Decision.** Each tick asks "which alerts are due and not yet raised?" rather than writing
"send at J-7" rows when a tender is pursued. Offsets are counted in **working days**, and only
the nearest unsent offset fires per tender.

**Why each part:**

- **Computed, not scheduled.** Deadlines move - that is frequently what an amendment *is*.
  Pre-scheduled rows would fire against the old date and would need a cleanup path on every
  amendment. Computing from the current deadline corrects itself with no cleanup at all.
- **Working days.** A J-3 alert on a Monday deadline must land on the preceding Wednesday. In
  calendar days it lands on Friday, giving the team no working time - which defeats the alert.
- **Nearest offset only.** A tender pursued five days before its deadline would otherwise emit
  J-14, J-7 and J-3 at once, which reads as noise and buries the one that matters.
- **Public holidays are not modelled.** Treating a holiday as a working day makes an alert fire
  *earlier*, which is safe; treating a working day as a holiday makes it fire later, which is
  not. Given that asymmetry the naive Monday-Friday rule is the correct default until a
  per-country calendar exists (FR/BE/LU differ).

Idempotency comes from the `notifications` row, written in the same transaction as the send job:
a duplicate tick cannot double-send, and a crash between the two cannot lose an alert.

---

## ADR-0009 — Append-only tables get an explicit erasure escape hatch

**Date:** 2026-08-12 · **Status:** accepted · **Relates to:** §10.6, §15.5

**Decision.** The `decisions` and `events` triggers refuse `UPDATE` unconditionally, and
refuse `DELETE` unless `app.allow_purge = 'on'` is set for the transaction. That flag is set
only by `withPurgeContext`, used by the org-deletion pipeline and by test fixtures.

**Why.** The first implementation blocked `DELETE` outright, which collided with a legal
obligation: §15.5 requires personal data to be purged within 30 days of org deletion, and
`decisions` cascades from `tenders`. An unconditional block made any org that had ever
recorded a decision permanently undeletable — immutability defeating erasure.

Discovered by the test fixture failing to clean up, which is the useful kind of test
failure: the invariant was right, and its interaction with erasure was not thought through.

**Why a flag rather than a privileged role.** The flag is transaction-local, so it cannot
leak into a later request on a pooled connection, and every use is a call to one greppable
function. A role-based carve-out would be ambient and much harder to audit.

---

## ADR-0008 — Matching score computed in Python; the seed carries a declared stand-in

**Date:** 2026-08-12 · **Status:** accepted · **Relates to:** §8.2

`services/ingestion/bidpilot_ingestion/matching.py` is the single scoring authority. The
TypeScript seed contains a small, deliberately simplified score used only to make the demo
inbox plausible, and it says so at the definition. Two full implementations of a scoring
function would drift, and the one users see would be the wrong one.

**Revisit** when the API needs to score on the write path: expose scoring through the
ingestion service rather than porting it.

---

## ADR-0013 — The notifier is Python, not TypeScript

**Date:** 2026-08-13 · **Status:** revisit-at-phase-3 · **Relates to:** §17.2, §12.4

**Decision.** Notification delivery (`notify.send`, `digest.daily`) runs in the Python ingestion
worker. §17.2's topology diagram places the notifier in TypeScript.

**Why deviate.** A TypeScript notifier would need its own implementation of the queue's claim
semantics - `FOR UPDATE SKIP LOCKED`, attempt counting, capped backoff, dead-lettering, stale-claim
recovery. Two implementations of *that* would be a genuine hazard: the failure mode of a subtly
different retry policy is a duplicated or a silently dropped deadline alert, which is precisely
the outcome §16 calls the worst the product has. One queue implementation is the more important
invariant than one language per concern.

**What is preserved.** Email copy is read from `apps/web/messages/*.json`, the same catalogue the
UI uses, so an email and the screen it links to cannot disagree and neither carries a hardcoded
string (§20.6).

**Revisit when** rich HTML email is needed - branded digests, or the studio's outputs. At that
point react-email in TypeScript is clearly the better tool, and the right move is to extract the
queue consumer into a shared contract first rather than reimplementing it.


---

## ADR-0014 — The worker's role must be *subject* to RLS, and that is enforced

**Date:** 2026-08-13 · **Status:** accepted · **Relates to:** §15.5, §17.6, ADR-0011

**Decision.** Three changes, one invariant: the role behind `DATABASE_URL` must be a plain
schema owner, exempt from nothing.

1. `db.assert_policy_bound()` refuses to establish tenant context on a connection whose role is
   SUPERUSER or BYPASSRLS. It is called from `set_org_context()` - Python's `withOrgContext` -
   so every per-org read and write in the ingestion service passes through it. One query per
   engine per process, because it sits on a loop over every org.
2. CI and docker-compose set `POSTGRES_USER: postgres` - an admin role used only for the
   privileged setup - and `bootstrap_roles.sql` creates the unprivileged `bidpilot` owner that
   `DATABASE_URL` points at. Migrations, seed and the whole suite run as that owner. The
   bootstrap ends by verifying it is not exempt, and fails if it is.
3. Both test suites assert their own role is policy-bound before asserting anything about
   isolation.

**Why this was needed.** ADR-0011 already said not to give the worker's login role BYPASSRLS,
and `sql/bootstrap_roles.sql` says it twice. Nothing checked it - and the forbidden
configuration is the *default* one: the postgres Docker image runs
`initdb --username="$POSTGRES_USER"`, so the role it creates is the cluster's bootstrap
superuser. That is what a stock `docker compose up` and a stock CI service container both hand
to `DATABASE_URL`.

Under such a role nothing looks wrong. `set_config('app.org_id', ...)` succeeds, every query
returns rows, every job reports success. What silently stops happening is the filtering, so a
per-org loop reads and writes every tenant's rows under one org's context - and the cross-tenant
suite whose entire purpose is to catch that passes regardless of what the policies say. This is
the failure mode §16 cares most about, arrived at by accident rather than by anyone deciding
anything.

**How it surfaced.** Simulating the CI database path locally, after fixing the pnpm setup step
that had been aborting both jobs before their tests ever ran. Until then the Python job had
never reached `pytest` with a database attached, so this had never been executed in CI at all.
With superuser, `test_deadline_alerts_stay_scoped_to_their_own_org` fails on a set comparison
that says nothing about why; that was the thread worth pulling.

**The first fix was wrong, and CI said so.** It kept `POSTGRES_USER: bidpilot` and ran
`ALTER ROLE bidpilot NOSUPERUSER` after the privileged setup. That works on any role *except*
the one it needed to work on: Postgres refuses to demote the role `initdb` created, with
`permission denied to alter role / the bootstrap user must have the SUPERUSER attribute`. It
passed locally only because the local `bidpilot` is not that cluster's bootstrap user, so the
rehearsal reproduced "is a superuser" without reproducing "is *the* superuser" - the distinction
the rule turns on. Hence the shape above: never demote, provision a separate owner from the
start, which is also how managed Postgres is set up. Two smaller findings came with it - the
bootstrap needs `GRANT CREATE ON DATABASE` because Prisma's engine issues `CREATE SCHEMA IF NOT
EXISTS` before applying anything, and every psql call in CI needs `-v ON_ERROR_STOP=1`, without
which psql exits 0 after printing ERROR and a broken bootstrap reports success.

**Two privilege facts fell out of running migrations as a correctly unprivileged owner**, both
invisible to a superuser because a superuser skips the checks:

- `ALTER FUNCTION ... OWNER TO bidpilot_platform` requires the caller to be able to `SET ROLE`
  to the new owner. `bootstrap_roles.sql` now grants that membership (defaulting to whoever runs
  it, overridable with `-v migration_role=`). Role attributes are not inherited through
  membership - BYPASSRLS applies only after an explicit `SET ROLE` - and the migration role owns
  every table anyway, so this widens nothing that matters.
- `ALTER ... OWNER TO` also checks the *incoming* owner's privileges: it needs CREATE on the
  object's schema, or it fails with `permission denied for schema public`.

Both grants live in `bootstrap_roles.sql` rather than in the migration, for the reason ADR-0005
already gives: they are privileged acts the migration role cannot perform on itself, so a
migration that tried would fail on exactly the deployments that need it. The migration instead
*verifies* both preconditions and fails with instructions naming the fix, the same
require-and-fail-loudly shape.

`bootstrap_roles.sql` is idempotent, so an existing database picks the grants up by re-running
it; nothing else is needed, because the migration itself has already succeeded there. Adding
those checks does change that migration's recorded checksum, which `prisma migrate deploy` -
the only migrate command this project runs - does not verify. Checked, not assumed: `deploy`
against a database holding the old checksum reports no pending migrations and applies nothing.

**Not chosen: making the test tolerant of a superuser.** It would have turned a real production
hazard into a green build, which is the outcome this whole entry exists to prevent.

---

## ADR-0015 — The web app gets a database role that can only authenticate

**Date:** 2026-08-13 · **Status:** accepted · **Relates to:** §17.1, §17.2, §15.1

**Decision.** Auth.js runs in the web app with the Prisma adapter, connecting as `bidpilot_auth`
via `DATABASE_AUTH_URL`. That role is granted DML on `accounts`, `sessions` and
`verification_tokens`, and SELECT/INSERT/UPDATE on `users`. It holds no grant on any other
table. Sessions are rows, not JWTs, and the API authenticates a request by looking the presented
token up rather than by verifying a signature.

**The tension.** §17.1 requires Auth.js; its email provider needs somewhere to keep single-use
tokens, so it needs an adapter, so the web app needs a database connection. §17.2 says
"web ↔ api only", and the reason given for it is real: tenant isolation should have exactly one
enforcement point.

**Why this resolves it rather than trading it away.** The reason §17.2 exists is that the web
app must not be able to read tenant data. Previously that was true because it had no connection
string - a property of configuration, held in place by discipline. Now it is true because
Postgres refuses: `SELECT count(*) FROM matches` as `bidpilot_auth` is `permission denied`, and
the RLS suite asserts exactly that for six tenant tables. The invariant did not weaken; its
enforcement moved from convention into the database, which is where the rest of this schema's
guarantees already live.

`users` is the one table holding anything personal that this role can reach, and it is
RLS-protected. The role's access is granted by three policies scoped `TO bidpilot_auth` rather
than by an exemption, so the tenant-facing policies on that table are untouched and the role
still cannot see a single row of org data. Authentication has to be able to find a person by
email before any org exists - that is what makes it authentication.

**Alternatives rejected.**

- **A custom adapter calling the API over HTTP.** Preserves §17.2 literally, but the endpoints it
  needs - create user, create session, consume verification token - are unauthenticated by
  nature, since they are what establish authentication. It converts a Postgres grant into a
  larger HTTP surface that would need its own shared secret. More moving parts, less enforcement.
- **JWT sessions with no adapter.** Would not remove the adapter (the email provider still needs
  one) and would make sign-out advisory until expiry. A session that cannot be revoked is a poor
  trade for one saved query.
- **Giving the auth role BYPASSRLS to read `users`.** Forbidden by ADR-0014 and unnecessary: a
  role-scoped policy is narrower and says what it means.

**What this bought, verified rather than assumed.** Sign-out deletes the session row, after which
the same token is refused by the API with 401. A magic link works once - the second use lands
back on sign-in, and `verification_tokens` is empty afterwards. The credential the API previously
accepted, a bare user id in a header, is now worth nothing; there is a test that asserts it.

**On the dependency.** `next-auth@5.0.0-beta.32`. Auth.js v5 is the App Router line and the only
one that works with Next 15 and React 19; v4 does not. The version is pinned exactly rather than
floated on a range, because a beta's patch releases are not bound by semver.

**Revisit when** SSO/SAML arrives (§15.1 puts it in Enterprise, P4). That is a bigger identity
story and the right moment to ask whether authentication should become its own service.

---

## ADR-0016 — The email connector extracts links and infers nothing else

**Date:** 2026-08-13 · **Status:** accepted · **Relates to:** §6.5, §21 (Phase 1), P1, P6

**Decision.** An inbound alert email becomes org-scoped **tender candidates**, one per distinct
link, in `analysis`. Links that match a source we ingest carry that attribution and join to the
existing notice; the rest carry their URL and nothing more. No other field is read from the
message body.

**Why not parse the notice out of the email.** Portal alerts often contain a title, a buyer and a
deadline, and it is tempting to read them. They are also prose written for humans, in per-portal
formats that change without notice, in a product where P3 makes a wrong deadline the worst defect
available. A regex that reads "clôture le 14/03" correctly for one portal and silently mis-reads
another is exactly the hallucinated fact P1 forbids. So the only thing taken is the element that
is structurally unambiguous: the URL. Where a link's own anchor text exists it becomes the title -
that is the portal's own label for that exact link, not an inference about it.

**Why candidates rather than notices.** A notice is market data, shared across every tenant
(§17.6). A forwarded email is one org's private mail, and it arrives with no CPV, no NUTS and no
amount - so as a notice it would fail every hard filter in §8.1 and reach nobody, while polluting
the commons. As an org-scoped tender it lands where a human can act on it, which is also what
`TenderOrigin.email` in the schema was always for.

**Three guards worth naming**, each of which was a bug first:

- **Lookalike hosts.** `notted.europa.eu` *ends with* `ted.europa.eu`, so the obvious suffix check
  would attribute an attacker-registered domain to a real TED notice. Host matching requires a dot
  boundary.
- **Boilerplate.** Unsubscribe, preferences, view-in-browser and social links are in every alert.
  Left in, one three-consultation email opened six workspaces. They are filtered, and the count is
  reported rather than dropped silently.
- **Address case.** Org ids are upper-case ULIDs and email local parts get normalised by some
  MTAs, so lower-casing the address - the obvious thing to do - destroyed every real org id. The
  tag's case is preserved, with a case-insensitive fallback against the platform's org list.

**The API accepts and does not parse.** `POST /v1/inbound/email` verifies a shared secret in
constant time, fails closed when none is configured, and enqueues. Parsing in the worker means a
malformed message costs a retry rather than an HTTP error that the provider answers by
re-delivering the same broken mail.

**Two things this shipped alongside**, because the feature was not usable without them: `GET
/v1/tenders` and the "Mes AO" screen, since both `pursue` and this connector were writing rows
nothing could read; and a fix to source health, which reported every push source as permanently
silent - a silence budget makes no sense for something nobody polls, and an alarm that is always
on is an alarm nobody reads.

**Revisit when** a portal's alert format is worth parsing properly for a large customer. The right
shape then is a per-portal parser with its own fixtures and its own tests, in the adapter family
structure §6.4 already describes - not a general-purpose email scraper.
