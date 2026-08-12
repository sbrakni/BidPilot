# BidPilot — Cahier des Charges (Product & Technical Specification)

**Version:** 1.0 — August 2026
**Owner:** Samir Brakni
**Audience:** AI-assisted development (Claude Code) + human reviewers
**Status:** Ready for implementation

---

## 0. How to use this document (instructions for Claude Code)

This is the single source of truth for building BidPilot. It is written to be executed phase by phase, not all at once.

- **Build order:** follow §21 (Build Roadmap). Do not start Phase N+1 before Phase N acceptance criteria pass.
- **Reading strategy:** load §0–§5 for context, then only the sections relevant to the phase you are building. Annexes are reference material.
- **Repo convention:** place this file at `docs/SPEC.md` in the monorepo. Maintain `docs/DECISIONS.md` (ADR log — every deviation from this spec must be recorded there with a reason) and a `CLAUDE.md` at the repo root pointing to both.
- **Keywords:** MUST / SHOULD / MAY are used in the RFC-2119 sense.
- **Language:** codebase, comments, commit messages in English. UI copy is French-first with English translations (i18n keys from day one, see §20.6).
- **Verify-at-build markers:** items tagged `[VERIFY]` are facts that move over time (API field names, regulatory thresholds, prices). Check them against the live source when you implement the feature; put the confirmed value in config/seed data, never hardcode.
- **Definition of Done (global, applies to every task):** typed code, unit tests for logic, migration + seed updated, lint clean, feature demoable via seed data, i18n keys extracted, no secret in code.

---

## Table of contents

1. Product vision & positioning
2. Market context & competitive landscape
3. Personas & core user stories
4. Scope
5. Domain primer (public procurement in 2 pages)
6. **F1 — Sourcing & aggregation engine** (the moat)
7. **F2 — Company profile & evidence vault**
8. **F3 — Matching & scoring engine**
9. **F4 — Tender analysis (DCE intelligence)**
10. **F5 — Go/No-Go decision engine** (the heart)
11. **F6 — Compliance matrix**
12. **F7 — Bid workspace & collaboration**
13. **F8 — AI response studio** (drafts, PPTX/HTML decks)
14. **F9 — Market intelligence & post-submission**
15. **F10 — Platform: accounts, billing, admin**
16. Non-functional requirements
17. Technical architecture
18. Data model
19. API surface
20. UX specification
21. Build roadmap & acceptance criteria (for Claude Code)
22. Business model
23. Risks & mitigations
24. Assumptions & open questions
Annex A — Source catalog (country by country)
Annex B — Canonical schemas (JSON)
Annex C — Glossary of French/EU procurement terms
Annex D — Regulatory thresholds (config table)
Annex E — AI prompts, guardrails & evaluation harness
Annex F — References

---

## 1. Product vision & positioning

### 1.1 One-liner

> **BidPilot finds every public tender a company can actually win — including the ones buried on obscure portals — tells it whether to bid, proves that every requirement has evidence behind it, and only then drafts the response.**

### 1.2 The problem

Public procurement is enormous — **12.7% of GDP across OECD countries in 2023** — and the OECD explicitly identifies administrative complexity as the main barrier keeping SMEs out of it. Concretely, an SME that wants to sell to the public sector faces four compounding problems:

1. **Fragmentation.** Tenders are scattered across TED (EU), national gazettes (BOAMP in France, e-Procurement in Belgium…), and a long tail of *profils d'acheteur* — buyer-side platforms operated by a dozen software vendors, plus regional platforms, sector platforms (health, rail, energy, social housing), legal-notice newspapers (JAL), and buyers' own websites. Below-EU-threshold tenders (the majority by count, and the most SME-accessible) are precisely the ones published in the most obscure places.
2. **Qualification cost.** Reading a DCE (tender pack: 100–400 pages across RC, CCAP, CCTP, annexes) takes hours. Most SMEs either respond to too few tenders, or waste days on tenders they were never eligible to win because of one eliminatory criterion on page 47.
3. **Proof management.** A compliant response is mostly an evidence problem: certificates, insurance attestations, financial statements, references, CVs — each with an expiry date, each demanded in a slightly different form by each buyer.
4. **Production cost.** The mémoire technique and the pitch deck are rewritten from scratch each time, under deadline pressure.

### 1.3 The core thesis (what BidPilot is and is not)

**BidPilot is NOT a text generator with a tender skin.** LLM text generation is a commodity; ten competitors do it. BidPilot's defensible value, in priority order:

1. **Exhaustive sourcing** — aggregate *all* relevant sources, including hidden ones, with a connector architecture designed for the long tail (§6). This is the moat: it compounds, it's operationally hard, and it's what makes every downstream feature trustworthy ("if it's not in BidPilot, it doesn't exist").
2. **The bid/no-bid decision** — an explainable eligibility verdict: eliminatory criteria detected, probability of being eligible, missing documents, estimated effort vs. deadline (§10). Saying "NO-GO, and here's why, in 90 seconds" saves more money than any generated paragraph.
3. **Evidence-first compliance** — every extracted requirement must be linked to a proof (document, reference, certification) or explicitly flagged as a gap. The invariant: **no orphan requirements** (§11).
4. Only then, **assisted production** — a grounded first draft of the mémoire technique, pre-filled admin forms, and generated presentation decks (PPTX/HTML) built from the client's context and the company's evidence library (§13).

### 1.4 Product principles (bind every design decision to these)

- **P1 — Never hallucinate a requirement or a proof.** Every AI-extracted fact carries a citation (document, page). Every generated claim carries an evidence reference or a visible `[TO PROVIDE]` flag.
- **P2 — Decision before production.** The UI always leads users through Go/No-Go before opening the response studio.
- **P3 — Deadlines are sacred.** Any screen showing a tender shows its countdown. Amendment detected → re-analysis triggered → team alerted.
- **P4 — Explainability.** Every score (match %, eligibility, win probability) exposes its breakdown. No black-box numbers.
- **P5 — Time-to-value < 5 minutes.** SIRET in → live matching tenders out (§15.3).
- **P6 — Human in the loop.** AI outputs are drafts with review states, never auto-submitted anywhere.

### 1.5 Non-goals (v1)

- No electronic submission/deposit on buyer platforms (legal signature, formal deposit) — BidPilot assembles the final package and deep-links to the buyer portal for upload.
- No marketplace / subcontractor matchmaking.
- No general-purpose CRM (light pipeline states only; integrate later).
- No private-sector RFPs in v1 (architecture must not preclude them — a source is a source).

---

## 2. Market context & competitive landscape

### 2.1 Market signals

| Signal | Value | Source |
|---|---|---|
| Public procurement share of GDP (OECD, 2023) | 12.7% | OECD Government at a Glance |
| EU notices published on TED | ~700k+/year | ted.europa.eu statistics |
| French notices on BOAMP | tens of thousands/year (national + JOUE mirror) | boamp.fr open data |
| SME barrier identified by OECD | administrative complexity | OECD |
| French below-threshold publicity regime | adapted publicity from €40k, BOAMP/JAL above €90k `[VERIFY]` | Code de la commande publique |

Implication: the addressable inventory for a FR+BE+LU launch is roughly 1,500–3,000 new relevant notices per day across sources before filtering — trivially small for our ingestion architecture, huge for a human.

### 2.2 Competitive landscape (France-centric, 2026)

| Category | Players (examples) | What they do | Gap BidPilot exploits |
|---|---|---|---|
| Veille (monitoring) incumbents | Vecteur Plus, Explore, Wanao, France Marchés, Doubletrade | Keyword/CPV alerts, human-curated feeds, legacy UX, per-seat pricing | No decision layer, no evidence link, weak below-threshold coverage, no API |
| AI response assistants | Tenderbolt, Olra, Maître AO, dossiersgagnants, Smart BTP | Upload a DCE → summary + draft memo | Start at the wrong end (production, not sourcing/decision); no aggregation; hallucination risk |
| International aggregators | OpenOpps, Tender Radar, GPC Gov | Broad TED/OCDS aggregation | Shallow FR long-tail coverage, no French admin-form logic, English-first |
| Buyer-side platforms | AWS/marches-publics.info, atexo, Dematis… | Publication & submission tools for buyers | They are our data sources, not competitors |

**Positioning statement:** the first *decision-first* bid platform for SMEs in FR/BE/LU: exhaustive coverage in its launch geographies (including below-threshold and renewal signals), an auditable go/no-go, and an evidence-linked response — at self-serve SaaS pricing.

---

## 3. Personas & core user stories

### 3.1 Personas

| Persona | Profile | Pain | Success metric |
|---|---|---|---|
| **Léa** — CEO, 35-person IT services company (ESN) | Answers 2 tenders/month, wins 1 in 6 | Finds tenders too late; wastes time on ineligible ones | Win rate ↑, hours per bid ↓ |
| **Marc** — Bid manager, training company (Qualiopi-certified) | Owns the whole response cycle alone | Chasing expiring attestations; rewriting memos | Zero missed deadlines; reuse of past content |
| **Sofia** — Ops director, maintenance/facilities SME (80 p.) | Multi-region, multi-lot tenders | Doesn't know which lots to pick; compliance risk | Confident go/no-go per lot |
| **Karim** — Sales rep (contributor role) | Feeds references and CVs | Asked for the same documents every time | One-time contribution, auto-reuse |

### 3.2 Core user stories (the product in 12 stories)

| # | As a… | I want… | So that… | Spec |
|---|---|---|---|---|
| U1 | new user | to enter my SIRET and get my company profile pre-filled + live matching tenders | I see value in < 5 min | §15.3 |
| U2 | bid manager | one inbox of new tenders scored for *my* company, across all portals | I never monitor 15 sites again | §6, §8 |
| U3 | bid manager | eliminatory criteria and eligibility probability surfaced before I read the DCE | I kill bad pursuits in minutes | §9, §10 |
| U4 | CEO | a one-page go/no-go brief with reasons and expected value | the team decision is fast and recorded | §10 |
| U5 | bid manager | the list of missing documents with time-to-obtain | no last-minute panic | §10.4 |
| U6 | bid manager | a compliance matrix where every requirement links to a proof | nothing is claimed without evidence | §11 |
| U7 | team member | tasks auto-created from required documents, assigned with due dates | the response runs itself | §12 |
| U8 | bid manager | a grounded first draft of the mémoire technique | I edit instead of writing from blank | §13 |
| U9 | bid manager | a branded PPTX/HTML presentation generated from the tender context | soutenance prep takes an hour, not a week | §13.4 |
| U10 | CEO | to see who wins tenders in my sector/region and which contracts expire soon | I anticipate instead of react | §14 |
| U11 | bid manager | alerts on deadlines, amendments and Q&A dates | I never miss a date | §12.4 |
| U12 | admin | roles, seats and billing self-serve | no procurement friction to buy BidPilot | §15 |

---

## 4. Scope

### 4.1 Geographic & source scope by phase

| Phase | Geography | Sources |
|---|---|---|
| Launch (P1–P3) | France, Belgium, Luxembourg + all-EU via TED | TED API, BOAMP API, publicprocurement.be, pmp.b2g.etat.lu, PLACE, top FR multi-buyer platforms, DECP awards |
| Wave 2 (P4) | FR long tail + EU institutions | Platform-template scrapers (atexo/AWS/Dematis/Interbat instances), regional portals, sector portals (health, rail, energy, housing), TED eTendering, EU Funding & Tenders |
| Wave 3 | UK, US | Find a Tender OCDS API, Contracts Finder API, SAM.gov API |
| Wave 4 | NL, DE, international orgs | TenderNed, service.bund.de, UNGM, NATO NSPA, World Bank |

### 4.2 Sector scope

Platform is sector-agnostic. Launch focuses GTM and fine-tuning on three **sector packs** (curated CPV sets + vocabulary + demo data):

- **IT & digital** — CPV 72* (IT services), 48* (software), 302* (hardware), 79511000 etc.
- **Training** — CPV 805* (training services), 79632000, 80533100 etc.
- **Maintenance & facilities** — CPV 50* (repair/maintenance), 45259000, 90910000 (cleaning), 71314100 etc.

Sector packs are **data, not code** (seed tables), so adding a pack costs zero engineering.

### 4.3 Language scope

- UI: French (default), English. Architecture ready for NL/DE (i18n keys, no hardcoded copy).
- Content: notices ingested in any EU language; normalized fields machine-translated to the org's working language on demand (cached), originals always shown alongside. `[P4]`

---

## 5. Domain primer (read this before coding anything)

The 2-page mental model of EU/French public procurement that the whole codebase shares. Full glossary in Annex C.

- A **buyer** (*acheteur / pouvoir adjudicateur*) publishes a **notice** (*avis*) announcing a **tender** (*consultation / appel d'offres*), possibly split into **lots**. Notices have types: **planning** (*avis de pré-information*, PIN), **competition** (*avis de marché*), **result** (*avis d'attribution*).
- Above EU thresholds (Annex D), publication on **TED** is mandatory in the **eForms** standard. Below thresholds, national rules apply — in France: free choice under €40k, "adapted publicity" €40k–€90k, **BOAMP or JAL** above €90k `[VERIFY]`. Below-threshold = where SMEs win, and where sourcing is hardest. This asymmetry is BidPilot's founding insight.
- The tender pack (**DCE**) contains: **RC** (règlement de consultation — the rules: deadlines, required documents, award criteria & weights, eliminatory conditions), **CCAP** (administrative clauses: penalties, payment, revision), **CCTP** (technical requirements), **AE/ATTRI1** (act of engagement), price forms (**BPU**, **DPGF**, **DQE**), and annexes.
- A response = **candidature** (proving who you are: DC1, DC2 forms or **DUME/ESPD**, certificates, references) + **offre** (price forms + **mémoire technique** — the technical proposal, scored against weighted criteria).
- Classification systems: **CPV** codes (what is bought), **NUTS** codes (where), **NAF/NACE** (bidder activity — used to suggest CPV).
- After award, French buyers must publish **DECP** (essential data: winner, amount, duration) — open data that powers our renewal radar (§14).
- Key dates in a tender lifecycle: publication → **questions deadline** → (site visit, sometimes mandatory & eliminatory) → **submission deadline** (hard, to the minute, buyer's clock) → award → standstill → signature.

**Vocabulary rule for the codebase:** English domain terms in code (`tender`, `notice`, `buyer`, `requirement`, `evidence`), French terms preserved as string literals where they are proper nouns (`"mémoire technique"`, `"DC1"`, `"CCTP"`). Never translate CPV/NUTS/DUME.

---

## 6. F1 — Sourcing & aggregation engine (the moat)

**Goal:** one canonical, deduplicated, always-fresh stream of every public tender relevant to our users, from official APIs down to the most obscure buyer portal. Everything downstream (matching, decision, generation) consumes only the canonical stream.

### 6.1 Source registry (sources as data, not code)

Every source is a row in the `sources` table, not a hardcoded integration:

```
source {
  code            "fr-boamp", "eu-ted", "be-bosa", "fr-maximilien"…
  country         ISO-3166 ("FR","BE","LU","EU","UK","US")
  tier            1 official-api | 2 platform | 3 long-tail | 4 signals
  kind            api | ocds | rss | scrape | email | manual
  adapter         registered adapter name + version
  base_url, auth  config JSON (secrets in vault, referenced by key)
  schedule        cron expression (per-source frequency)
  legal           { basis: open-license|tos-reviewed|robots-ok, notes, reviewed_at }
  health          green | degraded | silent | disabled
  metrics         last_run_at, last_success_at, notices_7d, error_rate
}
```

**Admin UI (internal):** source dashboard with freshness SLO per source. A Tier-1 source silent > 6h or Tier-2 silent > 24h pages the operator (alert). A parser whose extraction yield drops > 40% week-over-week is flagged `degraded` automatically. **This monitoring is not optional** — an aggregator that silently loses a source is lying to its customers.

### 6.2 Connector SDK (the contract every adapter implements)

Python interface, one adapter per source *family*:

```python
class SourceAdapter(Protocol):
    def fetch_since(self, cursor: Cursor) -> Iterator[RawNotice]:
        """Incremental fetch. MUST be idempotent, MUST respect per-source
        rate limits, MUST persist raw payload untouched."""
    def normalize(self, raw: RawNotice) -> CanonicalNotice:
        """Map to canonical schema (Annex B). MUST set provenance.
        MUST NOT invent values — absent means absent."""
    def fetch_documents(self, notice: CanonicalNotice) -> list[DocumentRef]:
        """Return DCE document URLs/files when publicly reachable,
        else deep-link only."""
```

Pipeline stages (each a separate queue job, restartable): `fetch → store raw → normalize → dedupe → enrich → index → match`. Raw payloads are kept forever (cheap, enables re-normalization when parsers improve).

Mandatory adapter behaviors: exponential backoff with jitter; declared User-Agent (`BidPilotBot/1.0 (+https://bidpilot.example/bot)`); HTML snapshot stored for every scraped notice (audit + parser regression tests); per-source concurrency 1 unless allowed; kill switch per source.

### 6.3 Tier 1 — Official APIs (launch backbone)

| Source | Access | Notes |
|---|---|---|
| **TED (EU)** | Search API v3: `POST https://api.ted.europa.eu/v3/notices/search` — **no API key required for search**; expert-query syntax; eForms JSON fields | Filter server-side: `place-of-performance IN (FRA BEL LUX)` + CPV families; also ingest **result** notices (awards) and **PIN**. `[VERIFY exact field names against eForms SDK]` |
| **BOAMP (FR)** | Opendatasoft API: `https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records` — licence etalab-2.0 | Poll every 30 min; covers national + below-threshold ≥ €90k + many voluntary publications. `[VERIFY dataset schema]` |
| **DECP (FR awards)** | Consolidated open data (data.gouv.fr / data.economie.gouv.fr) | Feeds §14 renewal radar: winner SIREN, amount, duration, dates |
| **publicprocurement.be (BE)** | Official BOSA e-Procurement platform; public search; RSS/exports where available, else polite scraping; above-threshold mirrored on TED | Since 2023 all Belgian levels (federal, regions, communes) publish here — one source, whole country |
| **pmp.b2g.etat.lu (LU)** | Public consultation search (atexo-type portal); low volume → scrape 2×/day; above-threshold on TED | Companion info portal: marches.public.lu |
| **PLACE / marches-publics.gouv.fr (FR state)** | Public search + RSS `[VERIFY]`; state tenders also on BOAMP/TED mostly | Valuable for direct DCE document access |

### 6.4 Tier 2 — The platform-template insight (how we cover "hidden" portals cheaply)

The French long tail is not 300 random websites. It is **~6 software vendors, white-labeled hundreds of times**:

| Vendor family | Recognizable by | Example instances |
|---|---|---|
| **atexo (Local Trust MPE)** | `?page=Entreprise.EntrepriseAdvancedSearch` URL pattern | Maximilien (Île-de-France), Mégalis Bretagne, Territoires Numériques BFC, Alsace Marchés Publics, mp74, Luxembourg PMP |
| **AWS (AW Solutions)** | `marches-publics.info` / `aws-achat` domains | Hundreds of communes, départements, hospitals |
| **Dematis** | `e-marchespublics.com` | Multi-buyer national platform |
| **Interbat** | `marches-securises.fr` | Multi-buyer national platform |
| **achatpublic.com** | own platform | Large local authorities |
| **Klekoon, atline, others** | own platforms | Long tail |

**Architectural consequence (MUST):** scrapers are written per *vendor template*, instantiated per *portal* via config (base URL + quirks). One atexo adapter ≈ 30+ portals. Adding a portal of a known family = adding a row, zero code. This is how "even very hidden ones" becomes operationally realistic.

### 6.5 Tier 3 — Long-tail & hidden sources

- **Self-hosted buyer profiles:** universities, hospitals (RESAH, UniHA, CAIH catalogs), utilities with their own supplier portals (SNCF, RATP, EDF group, Enedis, ADP), defense/research (CEA, CNES, Inria). Each = one config row on an existing adapter family, or a bespoke micro-adapter.
- **Social-housing buyers (bailleurs sociaux):** heavy tender volume, mostly on Tier-2 platforms — covered by family adapters; maintain a directory mapping buyer → platform.
- **JAL layer (legal-notice newspapers):** for €40k–€90k adapted-publicity notices `[VERIFY thresholds]`. v1: do not scrape the press; capture what surfaces via Tier-1/2. P4 option: partner with a press-data provider or integrate France Marchés-style JAL aggregation. Documented gap, shown transparently in coverage stats.
- **Email inbox connector (clever, cheap, launch-ready):** every org gets `sources+{org}@in.bidpilot.app`. Users subscribe that address to any portal's native alert emails; inbound parsing (SES/Postmark webhook) extracts links → fetch → normalize. Instantly "covers" any portal that can send email, including authenticated ones, with the user's own access. MUST ship in Phase 1.
- **Manual add / browser clip:** paste a URL (or forward an email) → BidPilot fetches, parses, creates the tender in the org workspace. Browser extension P4.
- **EU institutions:** TED eTendering (institutions' own calls) + EU Funding & Tenders portal — high-value, English, often missed by French SMEs.

### 6.6 Tier 4 — Signal sources (tenders that don't exist yet)

The truly hidden market is the one before publication:

- **PIN / avis de pré-information** (from TED/BOAMP) → "upcoming tender" objects with estimated timing.
- **Renewal radar:** DECP + TED award notices give contract start + duration → predicted end date → predicted re-tender window (start − 6 to −12 months for the buyer's preparation). Surfaced as *"Marché arrivant à échéance: [buyer] — [object] — attributed to [incumbent] in [year] for [amount]"* (§14.2).
- **Buyer purchasing programs:** large buyers publish annual purchasing plans (programmation achats / SPASER). P4: scrape a curated list of the top ~100 buyers' pages.

### 6.7 Deduplication & clustering

The same tender legitimately appears on TED + BOAMP + a Tier-2 platform. Users must see **one** card.

- **Entity resolution keys, in order:** (1) explicit cross-references (TED refs national publication ids and vice versa) when present; (2) buyer identity (SIREN or normalized name) + submission deadline (±1h) + title trigram similarity ≥ 0.6; (3) fallback: embedding similarity ≥ 0.92 + same country + deadline within 24h.
- Result: `tender_cluster` with one **canonical notice** (field-level merge: richest description wins, earliest publication date, union of documents/URLs) + all source references preserved and displayed ("Published on: BOAMP, TED, Maximilien").
- Clustering MUST be re-entrant (late-arriving duplicates merge into existing clusters) and reversible (bad merge → manual split, log kept as training data).
- **Amendments:** a re-fetched notice whose content hash changed ⇒ new `notice_version`, diff computed, linked tenders flagged `amended` → triggers §9 re-analysis + §12.4 alert.

### 6.8 Enrichment

On every canonical notice: buyer SIREN resolution (via French *Recherche d'entreprises* API), CPV labels (multilingual seed table), NUTS labels, estimated-amount normalization (parse "HT/TTC", ranges), language detection, embedding computation (§8), and document harvesting (fetch public DCE zips ≤ 500MB into object storage; else store deep link + `requires_account` flag).

### 6.9 Volumes & SLOs

- Ingestion scale-point: ~5,000 raw notices/day (all-EU TED ≈ 2–3k/day + national sources); design for 10×.
- Freshness SLO: Tier-1 notice visible in BidPilot ≤ 60 min after source publication; Tier-2 ≤ 6h.
- Coverage transparency (MUST): public status page listing connected sources + per-source freshness — turns the moat into visible trust.

### 6.10 Acceptance criteria (F1)

- Given TED + BOAMP adapters enabled, when a notice is published on BOAMP that also exists on TED, then within 6h BidPilot shows exactly one card with both source badges.
- Given a source goes silent 6h (Tier 1), then the ops channel receives an alert and the status page shows `degraded`.
- Given a DCE zip is publicly downloadable, then documents are stored and listed on the tender within 30 min of ingestion.
- Given an org forwards a portal alert email to its inbox address, then a tender card is created within 5 min with `source=email`.
- Raw payload of any notice is retrievable by ID (audit).

---

## 7. F2 — Company profile & evidence vault

**Goal:** a living model of the company — identity, capabilities, proofs — that powers matching (§8), eligibility (§10), the compliance matrix (§11) and generation (§13). If F2 is empty, BidPilot is a search engine; if F2 is rich, it's a bid team.

### 7.1 Company identity (auto-bootstrapped)

- Input SIRET/SIREN → fetch from *Recherche d'entreprises* API (free, no key): legal name, NAF/APE, address, headcount band, creation date, executives. `[VERIFY endpoint]` Belgian (BCE/KBO) and Luxembourgish (RCS) lookups: P4, manual entry meanwhile.
- Financials: revenue for last 3 fiscal years (manual entry v1; PDF import of liasses P4). Used for CA-minimum eligibility checks.
- Geography: home NUTS + intervention zones (multi-select regions/départements + "national/EU" toggles + max distance km).
- Activity: NAF→CPV suggestion table (seed data) + user-curated CPV families + free-text capability keywords + negative keywords ("we do NOT do…").

### 7.2 Evidence vault (the proof library)

Everything is an `evidence` object: `{ kind, title, file(s), issued_at, expires_at, issuer, scope, tags[], extracted_meta jsonb, embedding }`.

| Kind | Examples | Freshness rule |
|---|---|---|
| `admin` | KBIS/extrait, attestation fiscale, attestation URSSAF/sociale, casier, RIB, pouvoirs, assurance RC pro / décennale | URSSAF ~6 months, fiscale yearly, KBIS < 3 months at submission `[VERIFY per-doc rules — config table]` |
| `certification` | Qualiopi, ISO 9001/14001/27001, MASE, Qualibat, CyberSecurity labels | `expires_at` from certificate |
| `reference` | project sheets (see 7.3) + satisfaction certificates | prefer < 3 years old |
| `people` | CVs, diplomas, habilitations | review yearly |
| `content` | past mémoires techniques, methodology chapters, QSE policy, RSE policy, org charts | chunked + embedded for §13 retrieval |
| `template` | branded DOCX/PPTX templates, logo kit | n/a |

- **Freshness engine:** daily job flags evidence expiring ≤ 30 days → task + notification. Dashboard widget "Vault health: 92% — 2 documents expiring".
- Upload UX: drag-drop → AI classification suggestion (kind + dates extracted from the PDF) → user confirms. Never auto-trust extracted expiry dates (P1 principle).

### 7.3 Reference projects (structured, reusable)

`reference { client, client_type public|private, title, description, cpv[], amount, period, location, contact?, evidence_ids[], reusable_blocks[] }` — semantically indexed. Used by: matching (similar past work), eligibility (references requirement), studio (auto-pick the 3 most relevant references for a given tender, by CPV + semantic + recency + amount similarity).

### 7.4 Acceptance criteria (F2)

- SIRET entry pre-fills identity in ≤ 5 s; user confirms/edits.
- Uploading a Qualiopi certificate PDF suggests kind=certification with expiry date pre-read; saving without confirming dates is impossible.
- Vault-health widget counts expired/expiring correctly (unit-tested date logic, Europe/Paris TZ).
- A reference created with CPV 72* surfaces in the "suggested references" list of any 72* tender workspace.

---

## 8. F3 — Matching & scoring engine

**Goal:** rank the daily stream against each org's profile so the inbox contains only plausible tenders, each with an explainable score.

### 8.1 Three-stage funnel (cost-controlled)

| Stage | Mechanism | Runs on | Cost |
|---|---|---|---|
| 1. Hard filters | SQL: CPV families, countries/NUTS, deadline ≥ min days, amount range, procedure types, keyword include/exclude | every new canonical notice × every org watch profile | ~zero |
| 2. Semantic score | pgvector cosine: notice embedding vs. org capability embedding (profile text + won references corpus); hybrid with BM25 keyword score | stage-1 survivors | cheap |
| 3. LLM qualification | Small-model structured verdict: fit rationale, red flags, suggested lots — only for notices above semantic threshold OR user-opened | top candidates | metered |

### 8.2 Match score (0–100, explainable)

`score = 100 × Σ wᵢ·fᵢ` with visible factors: activity fit (semantic+CPV), geographic fit, size fit (amount vs. revenue heuristic: flag if estimated value > ~50% annual revenue), certification prerequisites present, buyer familiarity (past interactions), recency/deadline comfort. Default weights in config; per-org override sliders (P4). UI always renders the factor breakdown bar. **No single opaque number — P4 principle.**

### 8.3 Inbox mechanics

- States: `new → shortlisted | dismissed(reason) | pursued(→ tender workspace)`. Dismissal reasons (too big, wrong activity, no time, bad buyer…) are logged → feed weight tuning (P4 learning loop: simple logistic reranker trained on org feedback; v1 = static weights).
- Daily digest email (07:30 Europe/Paris, configurable): top N new matches + deadlines this week + vault alerts. Instant push/email for score ≥ 80 (configurable).
- Saved watch profiles: multiple per org (e.g., "Formation Qualiopi IDF", "Infogérance nationale > 200k").

### 8.4 Acceptance criteria (F3)

- A seeded org (IT services, IDF) receives ≥ 10 relevant matches from a 48h TED+BOAMP replay fixture, zero matches violating hard filters (unit + integration tests on fixtures).
- Every match card displays its factor breakdown; sum of displayed factors = displayed score.
- Dismiss with reason persists and excludes the notice from future digests.
- Stage-3 LLM never runs on notices failing stage 1 (cost guard, asserted in tests).

---

## 9. F4 — Tender analysis (DCE intelligence)

**Goal:** turn a 200-page DCE into structured, cited, verifiable data: the requirement register, key dates, criteria, and red flags. This is the foundation of Go/No-Go (§10) and the compliance matrix (§11).

### 9.1 Document pipeline

`collect (auto from §6.8 or user upload) → classify file (RC / CCAP / CCTP / AE / BPU-DPGF / DUME / annex / amendment) → extract text (native PDF/DOCX/XLSX; OCR fallback for scans, Tesseract + cloud-OCR option) → segment (page-anchored chunks, ~1k tokens, heading-aware) → persist chunks with page spans`.

Every downstream extraction stores `provenance = {document_id, page_from, page_to, quote}` — **P1: no citation, no fact.**

### 9.2 Structured extractions (versioned JSON schemas — Annex B)

Two LLM extraction passes over the segmented DCE (map-reduce, chunk-parallel):

**A. AdminExtraction** (mostly from RC + notice): buyer & contacts; procedure type; lots (id, title, CPV, estimate); **dates** (questions deadline, mandatory site visit + its date, submission deadline + local time + buyer timezone, offer validity); submission modalities (platform, format, signature, page limits, language, samples); **award criteria with weights** (price %, technical sub-criteria %); **eliminatory conditions** (mandatory visit, certifications required, minimum revenue, mandatory lots combinations…); required documents split candidature vs offre; financial clauses (price form, revision, penalties, advance, retenue de garantie); variants allowed; groupement/subcontracting rules; RSE/social clauses.

**B. RequirementExtraction** (RC + CCTP + CCAP): exhaustive register of atomic requirements: `{ ref "REQ-041", text, type eliminatory|selection|award|contractual|format, category, needs_evidence bool, provenance, confidence }`. Target quality gates (Annex E eval harness): **recall ≥ 0.90 overall, recall ≥ 0.95 on eliminatory items**, precision ≥ 0.85. Below-gate model/prompt changes MUST NOT ship.

Extraction UX: every field shows its citation on hover; `confidence < 0.7` renders an amber "verify" chip; user corrections stored as labeled data (gold set grows from real usage).

### 9.3 Red-flags detector

Heuristics + LLM pass producing warnings, each cited: spec locked to a brand/product; deadline unusually short vs. procedure norm; disproportionate requirements vs. amount (e.g., CA minimum > 2× estimate `[VERIFY legal ratio]`); incumbent named in DECP history with repeated wins (§14 data); mandatory visit already passed; estimate inconsistent across documents. Rendered as "Points de vigilance" on the tender.

### 9.4 Amendment diffing

New DCE version (§6.7) → re-run pipeline on changed files only → diff at requirement level (`added / removed / modified`) → notify assignees of impacted compliance rows (§11) and tasks (§12). Deadline change = red banner + recalculated countdowns everywhere.

### 9.5 Acceptance criteria (F4)

- Fixture corpus (Annex E): ≥ 15 real DCEs (IT/training/maintenance mix, FR+BE+LU) with hand-labeled gold extractions lives in `/fixtures/dce/`. CI runs the eval harness; merging a prompt/model change that drops metrics below gates fails CI.
- 200-page DCE analyzed end-to-end ≤ 30 min P95; per-analysis cost logged and ≤ €3 target at launch models `[VERIFY pricing]`.
- Every displayed extracted fact opens its source page on click (deep-link into the PDF viewer at the right page).
- OCR path: a scanned RC fixture yields dates & criteria correctly.

---

## 10. F5 — Go/No-Go decision engine (the heart)

**Goal:** answer "should we bid?" in minutes, with an auditable, explainable verdict. This is the feature the user described as the product's true value — treat it as the flagship.

### 10.1 Eligibility check (deterministic first, AI second)

Cross AdminExtraction + RequirementExtraction against the F2 profile:

| Check | Logic | Output |
|---|---|---|
| Certifications required | required set ⊆ valid (non-expired) org certifications? | pass / fail / expiring-before-deadline |
| Minimum revenue | org 3-year revenue vs. required threshold | pass / fail / unknown (missing data) |
| References required | count & similarity of org references vs. demanded ("3 similar projects < 3 years") | pass / partial / fail |
| Mandatory visit | visit date still reachable? | pass / fail(date passed) |
| Admin documents | required candidature docs vs. vault (valid on deadline date, not today) | ready / missing list |
| Capacity | headcount, equipment, habilitations vs. demands | pass / fail / unknown |
| Geography/lots | intervention zones vs. execution places per lot | per-lot verdict |

**Eligibility probability** = calibrated aggregate over checks (hard fail on any eliminatory ⇒ ≤ 5% with the blocker named). Every verdict lists its reasons with citations. Unknowns are surfaced as questions, never guessed (P1).

### 10.2 Winnability estimate (v1 honest heuristics, clearly labeled "estimate")

Factors: criteria structure (price weight vs. our positioning), incumbent presence & tenure (DECP/TED history), similarity to past won/lost tenders, lot size vs. company size sweet spot, buyer's SME track record (share of DECP awards to <250-headcount suppliers). Output: `low / medium / high` + factors — not a fake percentage. Upgrade path P4: calibrated model on accumulated outcomes.

### 10.3 Effort & value

- Effort estimate: from required deliverables (memo page limits, forms count, deck, samples) × content reuse rate (how much §7 content matches) → person-days band + feasibility vs. remaining days and team calendar.
- Expected value sketch: estimate × user-set margin % × winnability band − effort cost band. Shown as ranges; inputs editable inline.

### 10.4 Missing documents plan

For each missing/expiring item: what, why needed (citation), **time-to-obtain** (config table: attestation fiscale = same day online; ISO cert = months ⇒ effectively NO-GO), responsible, one-click task creation (§12).

### 10.5 The decision brief & workflow

One screen (printable/PDF export, ≤ 1 page): recommendation **GO / NO-GO / GO-IF** (conditions listed), eligibility %, blockers, winnability, effort, deadline runway, missing docs, red flags, suggested lots. Team workflow: request opinions (vote + comment), decision recorded `{verdict, rationale, decided_by, at}` — immutable log (auditability = enterprise trust). Dashboard tracks decision speed and hit rate over time (§14.4).

### 10.6 Acceptance criteria (F5)

- Org lacking a required Qualiopi ⇒ brief shows NO-GO with the certification blocker cited to the RC page demanding it.
- Org missing only a fresh attestation ⇒ GO-IF with the document task pre-created.
- Brief renders ≤ 10 s from completed analysis; export to PDF works.
- Decision log immutable (update forbidden at API level; superseding decisions create new records).
- Every number on the brief traces to a visible factor list (no orphan numbers).

---

## 11. F6 — Compliance matrix

**Goal:** the requirement register becomes a living table where every line ends in a proof. The invariant that defines BidPilot: **no orphan requirements.**

### 11.1 The matrix

Auto-generated from RequirementExtraction on tender pursuit. Columns: requirement (text + citation link) · type (eliminatory badge red) · owner · response note · evidence links (vault items, reference projects, draft sections) · status `covered / partial / missing / not-applicable(justified)` · reviewer ✓.

- Filters: by type, status, owner, document of origin. Group by DCE document or by category.
- Progress bar: % covered, with eliminatory items weighted visually (one red item keeps the bar red).
- **Submission gate:** package export (§12.5) is blocked while any eliminatory requirement is `missing` — override requires Owner role + written justification (logged). Non-eliminatory gaps produce a warning list.
- Export: XLSX (buyers often ask for a filled conformity table — match their format when a template is provided in the DCE `[P3]`) and PDF.
- Amendment diffs (§9.4) re-flag impacted rows to `review-needed` without losing owner/notes.

### 11.2 Acceptance criteria (F6)

- Matrix rows = requirement register rows (1:1, tested on fixtures).
- Linking a vault evidence to a row flips status to covered only after owner confirmation.
- Export blocked with a named eliminatory gap; override path logs user + justification.
- XLSX export opens clean in Excel (columns, encoding, no truncation).

---

## 12. F7 — Bid workspace & collaboration

**Goal:** once GO, run the response like a project — dates, tasks, documents, questions — with zero deadline risk.

### 12.1 Workspace anatomy (tabs)

`Synthèse` (brief + countdowns) · `Analyse` (F4 outputs) · `Go/No-Go` (F5) · `Conformité` (F6) · `Documents` (DCE viewer + our response files) · `Rédaction` (F8 studio) · `Équipe` (tasks, activity).

### 12.2 Auto-generated task plan

On GO: tasks created from required documents & deliverables (one per candidature doc, memo sections, price forms, deck, submission rehearsal), with computed due dates back-planned from the deadline (config: buffer = 2 working days before deadline; platform upload rehearsal = day −3 for first-time buyers' platforms). Kanban + list + calendar views; assignees; @mentions; comments; activity log. ICS feed per user + workspace.

### 12.3 Q&A management

Track questions to the buyer: draft (AI-assisted, §13.6), sent date, buyer answer, impact links to requirements. Countdown to questions-deadline shown beside the main deadline (it's the date teams miss most).

### 12.4 Notifications & escalation

Events: new high-score match, deadline J−14/J−7/J−3/J−1 (working-day aware, Europe/Paris + buyer TZ), amendment detected, task overdue, evidence expiring, Q&A answer published, decision requested. Channels v1: in-app + email; v1.1: Slack/Teams webhooks. Per-user preferences; org-level escalation rule (unacknowledged J−3 alert → notify Owner).

### 12.5 Final package assembly

Checklist mirroring the RC's demanded structure (candidature/offre, naming rules, formats, signature requirements listed) → generate the zip in the demanded folder layout with a manifest page → final gate = compliance check (§11) + file presence + size limits → "Ready to submit" state + deep link to the buyer platform. BidPilot does NOT submit (non-goal §1.5) but records `submitted_at` + proof screenshot upload for the log.

### 12.6 Acceptance criteria (F7)

- GO on a fixture tender creates ≥ 8 sensible tasks with back-planned dates (unit-test the date math incl. weekends).
- Amendment on fixture → banner + notification + impacted rows flagged within 5 min.
- Package zip matches demanded layout on the fixture RC (snapshot test).
- A J−3 alert unacknowledged for 24h escalates to Owner (integration test with time mock).

---

## 13. F8 — AI response studio

**Goal:** produce the response *materials* — grounded draft memo, pre-filled admin forms, branded presentation decks (PPTX + HTML) — from tender context + evidence vault. Drafts, never final; cited, never invented.

### 13.1 Grounding rules (non-negotiable, P1)

- Generation context = tender extractions (§9) + selected vault content (§7) + org style profile. **The model is instructed to use only provided material.**
- Every generated paragraph carries machine-readable source refs (evidence IDs / requirement IDs). The renderer strips or flags any unreferenced factual claim as `[À COMPLÉTER]`.
- Numbers (headcount, revenue, certifications, dates) are template-injected from structured data, never free-generated.
- Visible AI-draft watermark until a human marks the section reviewed.

### 13.2 Mémoire technique drafting

- **Outline first:** derived from RC demands (imposed plan if the RC specifies one — common) + award criteria weights (a 60% technical-value criterion with named sub-criteria ⇒ sections mirror sub-criteria and their weights). User approves outline before section drafting (cost + control).
- **Section drafting:** per-section generation with retrieved chunks (past memos, methodology, QSE/RSE content, selected references, CVs). Tone presets (sobre institutionnel / commercial / technique). Respect page limits from RC (soft budget by section, live word/page count).
- **Reference picker:** §7.3 recommender proposes the top references per section; drag to insert as formatted project sheets.
- Editor: block-based rich editor with per-section chat ("resserre cette section", "ajoute un tableau de jalons"), tracked AI/human status per block, comments, version history. Side panel: the requirements this section must answer (from §11 links) with live coverage ticks.
- **Export:** DOCX via org template (docxtpl-style merge: styles, header/footer, logo) + PDF. Page-limit validator on export.

### 13.3 Admin forms auto-fill

- **DC1 / DC2** data assembly from profile → filled PDFs `[VERIFY current CERFA layouts — keep form templates as versioned assets]`.
- **DUME/ESPD:** generate the request-matching response XML/JSON via the ESPD standard model `[P3, VERIFY schema version]`, plus a human-readable summary.
- Lettre de candidature, déclarations sur l'honneur: templated DOCX.
- Every filled field traces to its profile source; missing fields become tasks.

### 13.4 Presentation generator (PPTX + HTML) — the user-requested showpiece

Input: tender context (buyer, stakes, criteria), our approach (from memo outline/sections), branding kit (logo, colors auto-extracted from logo with manual override, fonts).

- **Deck plan** (editable before render): Contexte & enjeux du client (from CCTP/notice analysis — shows we understood *their* problem) · Notre compréhension du besoin · Approche & méthodologie · Équipe proposée (CV cards) · Références similaires (3 picked) · Planning & jalons · Garanties, SLA & engagements · Pourquoi nous (mapped to award criteria!).
- **PPTX renderer:** python-pptx against a master template system (org template upload P4; 3 built-in clean masters at launch). Charts (planning Gantt, org chart) drawn natively, not screenshots.
- **HTML renderer:** single-file HTML deck (inline CSS/JS, keyboard nav, print-to-PDF clean) — same content model, different renderer. Useful for soutenances where sending a link lands better than a 40MB pptx (link sharing via signed URL).
- Same grounding rules as 13.1 (deck claims cite evidence; ungrounded ⇒ placeholder).

### 13.5 Translation & multilingual

Respond-in-language helper: FR↔EN↔NL↔DE section translation preserving structure & citations `[P4]`; glossary pinning (never translate product names, certifications).

### 13.6 Q&A and clarification assistant

Detect ambiguities/contradictions in the DCE (cross-document: CCAP says 30-day payment, RC says 45) → suggest clarification questions before the questions deadline. This regularly saves bids and demonstrates seriousness to buyers.

### 13.7 Cost & metering

Token usage logged per generation `{org, tender, feature, model, tokens_in/out, cost}` → plan credits (§22). Model tiers: S (classification/routing), M (extraction/drafting default), L (final brief polish, complex reasoning) — abstracted behind one internal LLM gateway (§17.4) so models can be swapped by config.

### 13.8 Acceptance criteria (F8)

- Draft memo on fixture tender: every paragraph shows its refs; deliberately removing an evidence turns dependent claims into `[À COMPLÉTER]` (test).
- Imposed-plan RC ⇒ outline mirrors it exactly.
- DOCX export respects org template styles & page limit warnings; PPTX opens clean in PowerPoint & LibreOffice; HTML deck scores ≥ 90 Lighthouse a11y, prints clean.
- No generation runs on a tender without completed analysis (state machine enforced).

---

## 14. F9 — Market intelligence & post-submission

**Goal:** close the loop (learn from outcomes) and open the future (see the market before it's published).

### 14.1 Outcome tracking

On each submitted tender: result `won / lost / cancelled / no-answer`, our score & rank if communicated, winner & price when published (auto-matched from award notices/DECP), buyer feedback letter upload. Lessons-learned note prompt. Feeds §8 learning loop and win-rate analytics.

### 14.2 Renewal radar (the hidden-market feature)

From DECP + TED award data (§6.6): contracts in the org's CPV/geo footprint with `end_estimate = signed_at + duration` in the next 3–18 months → cards: buyer, object, incumbent, historical amount, predicted re-tender window, confidence; subscribe → alerted when window opens or PIN/notice actually appears (auto-linked by §6.7 clustering). This tells users **who to call before the tender exists** — legal, public data, massively underused.

### 14.3 Buyer & competitor profiles

Buyer page: award history, favored suppliers, average lot sizes, SME share, payment reputation `[P4]`, upcoming renewals. Competitor page (any SIREN): where they win, with whom, typical amounts. Data source: DECP + TED awards only (public data — no scraped private info).

### 14.4 Analytics dashboard (org)

Win rate by sector/buyer-type/size band; decision funnel (matched → pursued → submitted → won); average effort per bid; deadline near-misses; pipeline value. Exportable.

### 14.5 Acceptance criteria (F9)

- DECP fixture ingested ⇒ renewal cards computed with correct end-date math (unit tests incl. missing-duration handling: mark `unknown`, never guess).
- An award notice matching a submitted tender auto-fills winner & price and prompts outcome confirmation.
- Analytics numbers reconcile with raw event counts (tested).

---

## 15. F10 — Platform: accounts, billing, admin

### 15.1 Tenancy & roles

Organizations (workspaces) with strict row-level isolation (§17.6). Roles: **Owner** (billing, overrides), **Admin**, **Bid manager**, **Contributor** (assigned tasks/sections only), **Viewer**. Invitations by email; multi-org membership supported (consultants). SSO/SAML: Enterprise tier, P4.

### 15.2 Billing (Stripe)

Subscriptions per §22 plans (monthly/annual), seat counting, AI-credit metering with soft caps (80% warning) and top-ups, EU VAT handling via Stripe Tax, invoices, self-serve up/downgrade, 14-day trial (no card) with sample data + limited real matches. Grace on payment failure: read-only after 14 days.

### 15.3 Onboarding (the aha moment, scripted)

1. Email/SSO signup → 2. "Votre SIRET ?" → profile pre-filled (§7.1) → 3. pick sector pack + zones → 4. **instant: 20 live matching tenders with scores** (pre-computed against recent stream) → 5. "Voici pourquoi #3 vous correspond" (factor breakdown) → 6. prompt to upload first 3 evidences (checklist) → 7. daily digest opt-in. Target: signup → first relevant tender seen < 5 min (P5). Empty-vault state degrades gracefully: matching works, eligibility shows "unknown" chips prompting uploads.

### 15.4 Admin & ops (internal back-office)

Source dashboard (§6.1), org support view (impersonation with consent + audit), feature flags, prompt/model config registry (§17.4), eval-harness results, cost dashboards (LLM spend per org/feature), GDPR tooling (org export, deletion pipeline).

### 15.5 Acceptance criteria (F10)

- RLS proven by tests: user of org A can never read org B rows through any API path (automated cross-tenant test suite).
- Stripe webhooks idempotent; plan change reflects in entitlements ≤ 1 min.
- Trial without card reaches the 20-matches moment with no billing prompt.
- Org deletion: personal data purged ≤ 30 days, files included; anonymized usage metrics may remain (documented).

---

## 16. Non-functional requirements

| Domain | Requirement |
|---|---|
| **Performance** | Inbox/search P95 < 300 ms; tender page P95 < 1 s; DCE analysis P95 < 30 min; deck/memo section generation < 60 s per section |
| **Scale (design point)** | 5k raw notices/day (10× headroom); 2M canonical notices stored by year 2; 500 orgs year 1; DCE storage ~50 GB/1000 tenders → object storage with lifecycle (raw DCEs to cold storage after deadline + 1 year) |
| **Availability** | 99.5% app; ingestion downtime invisible to users (queue catch-up); RPO ≤ 24 h (daily backups + WAL), RTO ≤ 4 h; deadline alerts are the most critical path — alert scheduler MUST be redundant (missed-alert = worst product failure) |
| **Security** | TLS everywhere; encryption at rest; per-tenant isolation via Postgres RLS (§17.6); secrets in a vault (not env-committed); signed short-lived URLs for files; OWASP ASVS L2 posture; rate limiting; audit log of sensitive actions; dependency & container scanning in CI |
| **GDPR / data residency** | EU-hosted (provider in §17.1); DPA + subprocessor list page; personal data mapped (users, contacts in notices, CVs) with retention policy; org export & deletion (§15.5); CV/personal evidence encrypted at rest with per-org keys `[P4]`; AI subprocessors: EU endpoints where available, disclosed |
| **Scraping ethics/legal** | Per-source legal review recorded in registry (§6.1): prefer official APIs & open licenses (etalab-2.0, TED reuse with source acknowledgment); respect robots.txt & ToS; identified bot UA; throttle ≤ 1 req/s/source default; store minimal factual metadata + deep links for restricted sources; DCE files stored only when publicly accessible without authentication; per-source kill switch; respond to operator complaints < 48 h (contact on bot page). Sui generis database-right risk (art. L342 CPI) mitigated by: factual-data extraction, no wholesale republication, source attribution & linkback |
| **Accessibility** | WCAG 2.1 AA target (RGAA-friendly — public-sector-adjacent buyers care); full keyboard nav on tables/matrix |
| **i18n / TZ** | All copy in locale files; dates stored UTC, displayed in org TZ with buyer-TZ warnings on deadlines; currency display per notice currency |
| **Observability** | OpenTelemetry traces on pipeline stages; structured logs; Sentry (front+back); metrics dashboards: ingestion freshness, queue depth, LLM cost/day, alert delivery success. On-call alert rules for: source silent, queue stalled > 15 min, alert-send failures, error-rate spike |
| **Quality gates** | CI: typecheck, lint, unit, integration (dockerized PG/Redis/MinIO), E2E happy paths (Playwright), AI eval harness (Annex E) on extraction-affecting changes; seed script creates a full demo org |

---

## 17. Technical architecture

### 17.1 Stack decision (chosen, with reasons)

| Layer | Choice | Why |
|---|---|---|
| Monorepo | **pnpm workspaces + Turborepo** | One repo for Claude Code; shared types; atomic changes |
| Web app | **Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query/Table** | Fast to build dense B2B UI; SSR for shareable tender pages; hiring-proof |
| API | **NestJS (TypeScript) REST + OpenAPI** (generated client for web) | Structured modules mirror F1–F10; OpenAPI doubles as public-API docs (§19); background-job friendly |
| Data/AI services | **Python 3.12 + FastAPI (internal only)**: `ingestion`, `analysis`, `generation` services | Best ecosystem for scraping (httpx, Playwright), parsing (pdfplumber, python-docx, openpyxl, Tesseract), documents (python-pptx, docxtpl), LLM tooling |
| DB | **PostgreSQL 16 + pgvector** (+ FTS with `unaccent`) | One database for relational + vector + full-text at our scale; boring, perfect |
| ORM | **Prisma** (TS side; owns migrations) / **SQLAlchemy Core** (Python, read/write via the same schema) | Single migration authority (Prisma), Python treats schema as given |
| Queue | **Postgres-backed job queue** (`jobs` table, `FOR UPDATE SKIP LOCKED`), one worker lib per language, cron via `pg_cron` or scheduler service | Language-agnostic (TS *and* Python consumers), transactional enqueue with business writes, one less infra piece; migrate to Redis/BullMQ only if throughput demands `[decision recorded in DECISIONS.md]` |
| Object storage | **S3-compatible EU** (Scaleway Object Storage prod; MinIO dev) | GDPR residency; presigned URLs |
| Cache | **Redis** (sessions, rate limits, hot counters) | Standard |
| LLM | **Claude API** via internal gateway; embeddings: **voyage-multilingual** `[VERIFY current best multilingual embedding + EU endpoint availability]`; OCR: Tesseract default, cloud OCR optional flag | Structured outputs (tool-use JSON) for extractions; model tiers S/M/L by config |
| Auth | **Auth.js** (email magic link + Google/Microsoft OAuth) + org invitations; argon2 where passwords exist | SME-friendly login reality (Microsoft accounts everywhere) |
| Billing | **Stripe** (subscriptions, Tax, customer portal) | §15.2 |
| Email | **Postmark or Resend** (transactional + inbound parsing for §6.5 email connector) | Inbound webhook is a hard requirement — verify inbound support on chosen provider |
| Deploy | **Docker Compose** dev; prod: EU VPS/PaaS (Scaleway/Hetzner + Coolify, or Fly.io EU regions) — start simple, one box + managed PG is fine to ~thousands of users | Cost & simplicity; no Kubernetes before it hurts |
| CI/CD | GitHub Actions: lint/test/eval gates → staging auto-deploy → prod manual gate | §16 quality gates |

### 17.2 Service topology

```
                        ┌────────────────────────────────────────┐
   Users ──────────────▶│  web (Next.js)                         │
                        └────────────┬───────────────────────────┘
                                     │ REST (OpenAPI client)
                        ┌────────────▼───────────────────────────┐
   Stripe/Webhooks ────▶│  api (NestJS) — authz, orgs, tenders,  │
   Inbound email ──────▶│  matches, matrix, tasks, billing, API  │
                        └───────┬──────────────┬─────────────────┘
                                │ SQL          │ enqueue (jobs table)
                        ┌───────▼──────┐   ┌───▼───────────────────────────┐
                        │ PostgreSQL   │◀──┤ workers                       │
                        │ (+pgvector,  │   │  • ingestion (py): adapters,  │
                        │  FTS, jobs)  │   │    normalize, dedupe, enrich  │
                        └───────┬──────┘   │  • analysis (py): doc pipeline│
                                │          │    extractions, red flags     │
                        ┌───────▼──────┐   │  • generation (py): memo,     │
                        │ S3 (files)   │◀──┤    pptx/html, forms           │
                        └──────────────┘   │  • notifier (ts): alerts,     │
                        ┌──────────────┐   │    digests, escalations       │
                        │ Redis        │◀──┤  • scheduler: cron → jobs     │
                        └──────────────┘   └───────────┬───────────────────┘
                                                       │ HTTPS
                                    TED · BOAMP · portals · LLM gateway · OCR
```

Communication rules: web ↔ api only; workers ↔ DB/queue/S3 only (no worker HTTP endpoints except health); api never calls external portals inline (always via jobs) — keeps request latency flat.

### 17.3 Pipeline orchestration

Job kinds (queue topics): `source.fetch`, `notice.normalize`, `notice.dedupe`, `notice.enrich`, `notice.match`, `docs.fetch`, `docs.extract`, `analysis.admin`, `analysis.requirements`, `analysis.redflags`, `gonogo.compute`, `gen.outline|section|deck|forms`, `notify.send`, `digest.daily`, `vault.freshness`, `awards.ingest`, `renewals.compute`. Retries with capped backoff + dead-letter table + ops UI to replay. Job payloads are IDs, never blobs. Idempotency keys on every job (`kind + entity_id + version`).

### 17.4 LLM gateway (internal package `packages/ai`)

Single entry point for all model calls: model-tier registry (S/M/L → concrete model IDs in config), prompt templates versioned in-repo (Annex E), structured-output JSON-schema enforcement with auto-retry on validation failure, per-org token accounting, response caching by `(prompt_hash, model)` (dedup repeated analyses), EU endpoint pinning where offered, circuit breaker + fallback tier. **No service calls a model except through this gateway** — this is how cost, quality and swap-ability stay controlled.

### 17.5 Search & matching infra

- Notice FTS: generated `tsvector` (title + description + buyer, `unaccent`, french+english configs) with GIN index.
- Embeddings: pgvector `halfvec` HNSW index; notice embedding computed once at enrich; org capability embedding recomputed on profile change.
- Stage-1 filters compile watch profiles to SQL predicates; run per notice batch (matching is notice→orgs fan-out, batched every 5 min, instant path for score ≥ threshold pushes).

### 17.6 Multi-tenancy enforcement

- Every org-scoped table carries `org_id`; **Postgres RLS enabled** with policies keyed on `current_setting('app.org_id')`; api sets it per request after authz. Shared market data (notices, sources, awards) is global-read, never writable by tenants.
- File keys namespaced `orgs/{org_id}/…`; presigned URLs scoped + short-lived.
- Automated cross-tenant test suite (§15.5) runs in CI.

---

## 18. Data model (core — Claude Code derives full DDL from this + Annex B)

Conventions: `id` = ULID; timestamps `created_at/updated_at` everywhere; soft-delete only where legally needed; `jsonb` for source-shaped payloads; enums as PG enums.

### 18.1 Market data (global, tenant-read-only)

```
sources(id, code uq, country, tier, kind, adapter, config jsonb, schedule,
        legal jsonb, health, last_success_at, enabled)
raw_notices(id, source_id, external_id, fetched_at, payload jsonb, content_hash,
        uq(source_id, external_id, content_hash))
notices(id, cluster_id→tender_clusters, source_refs jsonb[], status active|amended|closed|awarded|cancelled,
        country, language, notice_type planning|competition|result,
        buyer_name, buyer_org_id?, buyer_siren?, title, description,
        cpv text[], nuts text[], procedure_type, amount_est numeric?, currency,
        published_at, deadline_at?, questions_deadline_at?, visit jsonb?,
        lots jsonb, urls jsonb, docs_available bool, version int,
        tsv tsvector, embedding halfvec(1024))
notice_versions(id, notice_id, version, payload jsonb, diff jsonb, created_at)
tender_clusters(id, canonical_notice_id, confidence, merged_from jsonb)
notice_documents(id, notice_id, kind rc|ccap|cctp|ae|price|dume|annex|amendment|other,
        title, file_key?, url, pages?, ocr_status, text_extracted bool)
award_records(id, source, buyer_siren?, buyer_name, supplier_siren?, supplier_name,
        title, cpv[], nuts[], amount, signed_at, duration_months?, end_estimate_at?, notice_id?)
cpv_labels(code, lang, label) · nuts_labels(code, lang, label) · naf_cpv_map(naf, cpv[], weight)
```

### 18.2 Org & profile

```
orgs(id, name, siren?, country, locale, tz, settings jsonb, plan, ai_credits_balance)
users(id, email uq, name, locale) · org_members(org_id, user_id, role, uq(org_id,user_id))
company_profiles(org_id uq, identity jsonb, revenues jsonb, headcount, zones jsonb,
        cpv_families text[], keywords text[], negative_keywords text[],
        capability_text, capability_embedding halfvec(1024))
watch_profiles(id, org_id, name, filters jsonb, alert_policy jsonb, enabled)
evidences(id, org_id, kind, title, file_keys text[], issued_at?, expires_at?,
        issuer?, meta jsonb, tags text[], embedding?, status valid|expiring|expired)
reference_projects(id, org_id, client, client_type, title, description, cpv[],
        amount?, period daterange, location?, evidence_ids[], blocks jsonb, embedding)
content_chunks(id, org_id, evidence_id, text, meta jsonb, embedding)  -- past memos etc.
```

### 18.3 Matching & workspace

```
matches(id, org_id, notice_id, watch_profile_id?, score int, breakdown jsonb,
        state new|shortlisted|dismissed|pursued, dismiss_reason?, uq(org_id, notice_id))
tenders(id, org_id, notice_id?, origin match|manual|email, stage
        analysis|decision|response|submitted|closed, title, deadline_at, meta jsonb)
analyses(id, tender_id, kind admin|requirements|redflags|gonogo, version, status,
        output jsonb, model, tokens_in, tokens_out, cost_cents, created_at)
requirements(id, tender_id, ref, text, type eliminatory|selection|award|contractual|format,
        category, needs_evidence bool, provenance jsonb, confidence, state active|removed|modified)
compliance_items(id, tender_id, requirement_id uq, status covered|partial|missing|na,
        owner_id?, note, evidence_refs jsonb, reviewed_by?, review_state)
decisions(id, tender_id, verdict go|nogo|goif, conditions jsonb, eligibility_pct,
        winnability, reasons jsonb, votes jsonb, decided_by, decided_at)  -- INSERT-only
tasks(id, tender_id?, org_id, title, kind, assignee_id?, due_at, status, source auto|manual)
qa_items(id, tender_id, question, status draft|sent|answered, sent_at?, answer?, impact_refs jsonb)
generations(id, tender_id, kind outline|section|memo_export|deck_pptx|deck_html|dc1|dc2|dume|letter|questions,
        status, input_snapshot jsonb, output jsonb?, file_key?, model, cost_cents, review_state)
memo_documents(id, tender_id, outline jsonb, blocks jsonb, version, page_budget jsonb)
renewal_signals(id, award_record_id, window daterange, confidence, state)
org_renewal_watches(org_id, renewal_signal_id, state)
```

### 18.4 Platform

```
subscriptions(org_id, stripe ids, plan, seats, status, period)
ai_usage(org_id, month, feature, tokens_in, tokens_out, cost_cents)
notifications(id, org_id, user_id?, kind, payload jsonb, channels, sent_at?, ack_at?)
events(id, org_id?, actor, kind, entity, payload jsonb, created_at)  -- audit, INSERT-only
jobs(id, kind, payload jsonb, state queued|running|done|failed|dead, run_at,
     attempts, locked_by?, locked_at?, idempotency_key uq, last_error?)
```

Key indexes: `notices(deadline_at) partial WHERE status='active'`; `notices USING gin(tsv)`; `notices USING hnsw(embedding)`; `matches(org_id, state, score desc)`; `jobs(state, run_at)`; `evidences(org_id, expires_at)`.

---

## 19. API surface (REST `/v1`, OpenAPI-generated docs)

Internal first; the same spec becomes the public API (Scale plan) with API keys + scopes + webhooks. Highlights only — CRUD detail derives from §18:

```
Auth        POST /auth/… (Auth.js)            Orgs      GET/PATCH /org, /org/members…
Profile     GET/PUT /profile · POST /profile/bootstrap {siret}
Vault       CRUD /evidences (+POST /evidences/{id}/files presign) · CRUD /references
Watch       CRUD /watch-profiles
Matches     GET /matches?state=&min_score= · POST /matches/{id}/dismiss|shortlist|pursue
Notices     GET /notices/{id} (global read) · GET /notices/{id}/sources
Tenders     CRUD /tenders · POST /tenders/import {url|file}
            POST /tenders/{id}/analyze · GET /tenders/{id}/analysis
            GET /tenders/{id}/requirements · PATCH /requirements/{id}
            GET/POST /tenders/{id}/decision · GET /tenders/{id}/brief.pdf
Matrix      GET /tenders/{id}/matrix · PATCH /matrix-items/{id} · GET /tenders/{id}/matrix.xlsx
Tasks       CRUD /tasks · GET /tenders/{id}/tasks · GET /calendar.ics (signed)
Studio      POST /tenders/{id}/generations {kind, params} · GET /generations/{id}
            PATCH /memo/{id}/blocks/{blockId} · POST /tenders/{id}/package
Intel       GET /renewals?cpv=&nuts= · GET /buyers/{id} · GET /analytics/…
Billing     GET/POST /billing/… (portal link, usage)
Webhooks(pub) match.created · tender.amended · analysis.completed · deadline.approaching
            · decision.recorded · generation.completed  (HMAC-signed, retried)
```

Rules: cursor pagination everywhere; `Idempotency-Key` honored on POSTs; problem+json errors; rate limits per key; all list endpoints filterable by the fields users see in UI (parity principle).

---

## 20. UX specification

### 20.1 Information architecture (left nav)

| Nav item (FR / EN) | Content |
|---|---|
| **Aujourd'hui** / Today | New matches inbox, deadlines this week, vault alerts, decisions pending |
| **Opportunités** / Opportunities | Full match list + filters + watch profiles; renewal radar tab |
| **Mes AO** / My tenders | Pursued tenders board by stage (analysis → decision → response → submitted) |
| **Bibliothèque** / Library | Evidence vault, references, content, templates, vault health |
| **Veille marché** / Market intel | Buyers, competitors, awards, analytics |
| **Réglages** / Settings | Org, members, watch profiles, notifications, billing, API |

### 20.2 Key screens (build in this order)

1. **Onboarding wizard** (§15.3) — 7 steps, progress bar, skippable after step 4.
2. **Match inbox** — card list: score ring with breakdown popover, title, buyer, amount, CPV chips, deadline chip (color by urgency), source badges, eliminatory pre-flags ("Qualiopi requise" detected from notice text), actions (shortlist/dismiss/pursue). Dense table toggle for power users. Keyboard: j/k navigate, s/d/p act.
3. **Tender workspace** (§12.1 tabs) — header always shows: title, buyer, amount, **countdown**, stage, next action.
4. **Go/No-Go brief** — one printable page (§10.5); big verdict banner; blockers list with citations; vote strip.
5. **Compliance matrix** — virtualized table (hundreds of rows), inline status edit, evidence-picker side panel, progress header.
6. **Studio** — three-pane: outline/nav · editor · context panel (requirements to cover, suggested references, citations). Generation actions per section with streaming preview.
7. **Deck preview** — slide thumbnails, regenerate per slide, brand kit switcher, export PPTX / open HTML.
8. **Renewal radar** — timeline cards by predicted window; subscribe toggle.
9. **Vault** — grid with freshness badges; upload dropzone; classification confirm modal.
10. **Analytics** — §14.4 widgets.

### 20.3 Design language

Clean, dense, calm B2B (linear-like sobriety): neutral background, one accent color, red reserved exclusively for eliminatory/deadline danger; tabular numerals for amounts/dates; empty states always teach the next action; skeleton loaders; every AI output visually tagged (sparkle icon + "Brouillon IA — à relire" chip) until reviewed. Component base: shadcn/ui; charts: Recharts; PDF viewer with page-anchor deep links (react-pdf).

### 20.4 Copy rules (FR-first)

Professional, precise, no hype; procurement vocabulary exactly as practitioners use it (*avis*, *DCE*, *mémoire technique*, *candidature*, *offre*, *soutenance*). Dates: `jeu. 12 mars 2027, 12:00 (heure de Paris)`. Amounts: `1 250 000 € HT`. All copy through i18n keys — reviewers can tune FR copy without code changes.

### 20.5 Mobile

Responsive read-and-react: inbox triage, alerts, task check-off, brief reading. Production work (matrix, studio) is desktop-first; no native app v1.

### 20.6 i18n mechanics

`next-intl` (web) + shared message catalog; ICU plurals; locale switch per user; server-rendered emails localized identically.

---

## 21. Build roadmap & acceptance criteria (execution plan for Claude Code)

Six phases. Each phase = shippable increment with a demo script. Do not parallelize phases; do parallelize within phases along the listed tracks.

### Phase 0 — Foundations (repo week)

Monorepo scaffold (§17.1 exactly); CI with all §16 gates (eval harness stub); Docker Compose (pg16+pgvector, redis, minio, mailpit); Prisma schema for §18 core; RLS bootstrap + cross-tenant test rig; Auth.js + orgs + invitations; design tokens + app shell + i18n plumbing; seed script (demo org, 3 personas, sample notices from fixtures).
**Exit:** `pnpm dev` boots everything; demo user logs in, sees empty states; CI green.

### Phase 1 — "Radar" (see everything)

Tracks: **(a) ingestion:** jobs infra; TED adapter; BOAMP adapter; email connector; normalize/dedupe/enrich; source dashboard. **(b) product:** profile bootstrap (SIRET); watch profiles; matching stages 1–2; inbox UI; tender pursue (workspace shell with Synthèse/Documents); daily digest + deadline alerts; onboarding wizard. **(c) platform:** Stripe plans + trial; status page.
**Exit demo:** fresh signup → SIRET → 20 live matches → pursue one → digest email next morning contains it. F1/F3/F2-lite acceptance criteria (§6.10, §8.4, §7.4-partial) pass on fixtures + one live 48h run.

### Phase 2 — "Decision" (the heart)

DCE fetch/upload + document pipeline + OCR; AdminExtraction + RequirementExtraction + citations UI; eval harness live with 15-DCE gold corpus (gates enforced); red flags; evidence vault complete + freshness engine; references; eligibility checks + missing-docs plan; Go/No-Go brief + decision workflow + PDF export; matching stage 3.
**Exit demo:** upload fixture DCE → in ≤ 30 min: dates, criteria, 120-requirement register with page-anchored citations → brief says GO-IF with the two missing attestations as tasks. §9.5 + §10.6 + §7.4 pass.

### Phase 3 — "Studio" (produce)

Compliance matrix full (incl. XLSX export + submission gate); workspace tasks auto-plan + Q&A + notifications/escalations; memo outline→sections with grounding + DOCX export; DC1/DC2 fill; deck generator PPTX + HTML; package assembly; amendment diffing end-to-end.
**Exit demo:** from the Phase-2 tender: matrix 100% eliminatory-covered → draft memo with citations → branded PPTX + HTML deck → package zip in demanded structure. §11.2 + §12.6 + §13.8 pass.

### Phase 4 — "Coverage & intelligence" (widen the moat)

Platform-template scrapers (atexo family first → instantly ~30 portals, then AWS, Dematis, Interbat, achatpublic); BE/LU deep coverage; DECP + award ingestion; renewal radar + buyer/competitor pages; analytics; learning loop v1 (dismissal-informed weights); Slack/Teams; public API + webhooks + docs; SSO.
**Exit:** coverage page shows ≥ 40 sources green; renewal radar demo (§14.5); public API consumed by a sample script.

### Phase 5 — "Expansion" (when traction demands)

UK (Find a Tender OCDS, Contracts Finder), US (SAM.gov), NL/DE; translation layer (§4.3, §13.5); multi-entity orgs; advanced win-probability model; browser extension.

### 21.1 Cross-phase engineering rules

- Ship behind feature flags; keep `main` deployable.
- Every adapter lands with: fixture snapshots, parser regression test, health metrics, legal row filled.
- Every AI feature lands with: prompt in registry, eval cases, cost logging, review-state UI.
- Every schema change: migration + seed update + (if user-visible) changelog entry.

---

## 22. Business model (indicative — validate with first 20 customers)

| Plan | Price `[VERIFY market]` | For | Includes |
|---|---|---|---|
| **Découverte** | 0 € | taste | 1 watch profile, 5 matches/week visible, no AI analysis, community support |
| **Starter** | 99 €/mo (annual −20%) | solo bid manager | Full FR+EU sourcing, unlimited matches, 5 DCE analyses/mo, matrix, vault, 1 user +1 viewer |
| **Pro** | 299 €/mo | SME team | 5 seats, unlimited analyses (fair use), studio (memo+decks) with AI-credit pool, intel & renewals, integrations |
| **Scale** | 799 €/mo | multi-team / consultancies | 15 seats, API + webhooks, SSO, multi-entity, priority support, custom connectors (1 included) |

AI credits: metered token cost × margin, pooled per org, top-ups self-serve; analyses priced in credits so heavy DCEs are sustainable. Positioning check: veille incumbents charge €100–300+/mo for alerts alone — BidPilot at €299 replaces veille + analyst hours + a writing tool.

---

## 23. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Scraper fragility (portal redesigns) | High | Template-family adapters (§6.4); snapshot regression tests; health monitoring + fast-fix ops loop; email connector as universal fallback |
| Legal pushback from a portal | Medium | §16 scraping policy; per-source legal registry; metadata+deep-link mode; official APIs preferred; partnerships when volume justifies |
| Missed eliminatory criterion (product's worst failure) | **Critical** | 0.95 recall gate (Annex E); eliminatory items double-extracted (two prompts, union); "verify" chips below confidence; visible citations; user correction loop; never claim exhaustiveness in UI ("détectés: N — vérifiez le RC") |
| Missed deadline alert | **Critical** | Redundant scheduler, delivery tracking + escalation (§12.4), ICS as belt-and-braces |
| LLM cost blowout | Medium | Tiered pipeline (§8.1), gateway caching, per-org metering, outline-approval before drafting |
| Hallucinated response content | High | Grounding rules §13.1, uncited-claim stripping, review states, watermark |
| Cold-start (empty vault ⇒ weak magic) | Medium | SIRET bootstrap, open-data prefill, guided first-3-uploads, value works vault-empty (radar + decision-lite) |
| Incumbent competitors add AI | Medium | Moat = coverage + decision trust + evidence graph, not text generation; move fast on Tier-2/3 coverage |
| Solo-founder bus factor / ops load | Medium | Boring infra (§17.1), runbooks in `/docs/ops`, alerting tuned to actionable only |

---

## 24. Assumptions & open questions (decisions taken to unblock build)

1. Product name **BidPilot**; domain TBD (check bidpilot.eu/.fr availability — rename is a config constant).
2. No e-submission v1 (§1.5) — revisit after PSP/eIDAS signature landscape assessment.
3. Pricing per §22 is a hypothesis; Stripe products seeded but flagged test-mode until validated.
4. Embedding provider & OCR cloud fallback: final pick at Phase-2 start (`[VERIFY]` benchmarks in Annex E harness).
5. JAL (press) layer deferred to Phase 4+ (documented coverage gap).
6. Working languages of first customers assumed FR; EN UI shipped anyway (BE/LU mixed-language reality).
7. Legal review of Tier-2 scraping per source before enabling in prod (registry field is mandatory, adapter refuses to run without it).

---

## Annex A — Source catalog (seed data for the `sources` table)

Access method legend: **API** (official), **OCDS** (open contracting JSON), **RSS**, **SCR** (polite scraping), **EML** (email connector), **[VERIFY]** = confirm at build.

### A.1 European Union

| Source | URL | Access | Tier | Notes |
|---|---|---|---|---|
| TED — Tenders Electronic Daily | ted.europa.eu · `api.ted.europa.eu/v3/notices/search` | API (keyless search) | 1 | eForms JSON; competition + PIN + award notices; all EU |
| TED eTendering (EU institutions) | etendering.ted.europa.eu | SCR/RSS [VERIFY] | 2 | Institutions' calls + documents |
| EU Funding & Tenders Portal | ec.europa.eu/info/funding-tenders | SCR [VERIFY API] | 2 | EC procurement & calls; high value for IT/training |

### A.2 France — national (Tier 1)

| Source | URL | Access | Notes |
|---|---|---|---|
| BOAMP | boamp.fr · `boamp-datadila.opendatasoft.com` dataset `boamp` | API (etalab-2.0) | National gazette incl. ≥ €90k MAPA; JOUE mirror |
| PLACE (État) | marches-publics.gouv.fr | SCR/RSS [VERIFY] | State + armed forces; DCE downloads |
| DECP (awards) | data.gouv.fr / data.economie.gouv.fr consolidated DECP | API/files | Fuel for renewal radar (§14.2) |
| Recherche d'entreprises | recherche-entreprises.api.gouv.fr | API (free) | SIRET bootstrap (§7.1) |

### A.3 France — multi-buyer platforms (Tier 2, template families)

| Family / platform | URL pattern | Instances (non-exhaustive) |
|---|---|---|
| atexo "Local Trust" | `…?page=Entreprise.EntrepriseAdvancedSearch` | Maximilien (IDF), Mégalis Bretagne, Territoires Numériques BFC, Alsace Marchés Publics, mp74 (Haute-Savoie), + dozens |
| AWS / AW Solutions | marches-publics.info, aws-achat.info | Hundreds of local authorities & hospitals |
| Dematis | e-marchespublics.com | National multi-buyer |
| Interbat | marches-securises.fr | National multi-buyer |
| achatpublic.com | achatpublic.com | Large collectivités |
| Klekoon | klekoon.com | Long tail |

### A.4 France — sector & central-purchasing (Tier 2/3)

| Source | Sector | Access |
|---|---|---|
| UGAP (as buyer of its own supply contracts) | generalist central body | via PLACE/BOAMP |
| RESAH · UniHA · CAIH | health | own portals + Tier-2 platforms [VERIFY per body] |
| SNCF (marches.sncf.com) · RATP · EDF group · Enedis · ADP | transport/energy/utilities | SCR per portal |
| CEA · CNES · Inria · universities (long tail) | research/education | Tier-2 platforms + own pages |
| Bailleurs sociaux (social housing, heavy volume) | housing | mostly Tier-2 families; maintain buyer→platform directory |

### A.5 Belgium & Luxembourg

| Source | URL | Access | Notes |
|---|---|---|---|
| BOSA e-Procurement | publicprocurement.be | SCR + exports [VERIFY API/RSS] | All Belgian levels since 2023; FR/NL bilingual |
| TED (BE/LU above threshold) | — | API | Covered by A.1 |
| Portail des marchés publics LU | pmp.b2g.etat.lu (+ marches.public.lu) | SCR (atexo family) | Low volume, 2×/day |

### A.6 Wave 3+ (UK, US, NL, DE, international)

| Source | Access | Notes |
|---|---|---|
| Find a Tender (UK) | OCDS API (`/api/1.0/ocdsReleasePackages`) | Above-threshold UK |
| Contracts Finder (UK) | API | Below-threshold UK |
| Sell2Wales · PublicContractsScotland · eTendersNI | SCR/API [VERIFY] | Devolved |
| SAM.gov (US) | Get Opportunities API (api.data.gov key) | Federal; NAICS mapping needed |
| TenderNed (NL) · service.bund.de (DE) | [VERIFY] | Wave 4 |
| UNGM (UN) · NATO NSPA · World Bank | portal accounts / SCR | International orgs — underused by SMEs |

### A.7 Universal fallbacks (any portal)

Email connector (§6.5) · manual URL import (§6.5) · browser extension (P5).

---

## Annex B — Canonical schemas (JSON, versioned in `packages/shared/schemas`)

### B.1 CanonicalNotice (v1)

```jsonc
{
  "$id": "bidpilot.notice.v1",
  "uid": "ntc_01J…",                       // ULID
  "cluster_id": "cls_01J…",
  "source_refs": [{ "source": "fr-boamp", "external_id": "24-123456",
                    "url": "https://…", "fetched_at": "…" }],
  "notice_type": "competition",             // planning | competition | result
  "status": "active",                       // active | amended | closed | awarded | cancelled
  "country": "FR", "language": "fr",
  "buyer": { "name": "Région Bretagne", "siren": "233500016",
             "contact": { "email": "…", "phone": "…" }, "address": {…} },
  "title": "Maintenance multitechnique des lycées — 4 lots",
  "description": "…",
  "cpv": ["50700000"], "nuts": ["FRH0"],
  "procedure": { "type": "open", "national_label": "AOO",
                 "framework": false, "reserved_sme": false },
  "lots": [{ "lot_id": "1", "title": "Lot 1 — Zone Rennes", "cpv": ["50700000"],
             "amount_est": 450000, "place_nuts": ["FRH03"] }],
  "amounts": { "estimated_total": 1800000, "currency": "EUR", "vat": "HT" },
  "dates": { "published_at": "2026-07-01T00:00:00Z",
             "questions_deadline_at": "2026-08-20T10:00:00Z",
             "deadline_at": "2026-09-01T10:00:00Z",
             "visit": { "mandatory": true, "dates": ["2026-07-15"] },
             "tz": "Europe/Paris" },
  "documents": [{ "kind": "rc", "title": "RC.pdf", "url": "…", "file_key": "…" }],
  "requires_account_for_docs": false,
  "provenance": { "adapter": "boamp@1.4.0", "normalized_at": "…" },
  "version": 2
}
```

### B.2 Requirement (v1)

```jsonc
{
  "$id": "bidpilot.requirement.v1",
  "ref": "REQ-041",
  "text": "Le candidat produira un certificat Qualiopi en cours de validité.",
  "type": "eliminatory",                   // eliminatory | selection | award | contractual | format
  "category": "certification",             // certification | financial | reference | staffing |
                                           // technical | administrative | format | rse | other
  "needs_evidence": true,
  "provenance": { "document_id": "doc_…", "document_kind": "rc",
                  "page_from": 12, "page_to": 12,
                  "quote": "…certificat Qualiopi en cours de validité…" },
  "confidence": 0.94,
  "lot_scope": ["1","2"]                   // empty = all lots
}
```

### B.3 GoNoGoBrief (v1) — summary object rendered by §10.5

```jsonc
{ "verdict": "go_if",
  "conditions": ["Obtenir attestation fiscale 2026", "Confirmer visite 15/07"],
  "eligibility": { "pct": 78, "checks": [{ "check": "certification.qualiopi",
      "status": "pass", "citation": {…} }, …] },
  "winnability": { "band": "medium", "factors": [ {…} ] },
  "effort": { "band_days": [8, 12], "deadline_runway_days": 21, "feasible": true },
  "missing_documents": [{ "what": "Attestation fiscale 2026",
      "time_to_obtain": "same_day", "task_id": "tsk_…" }],
  "red_flags": [{ "kind": "incumbent", "note": "Titulaire sortant: X (2 renouvellements)",
      "citation": {…} }] }
```

---

## Annex C — Glossary (seed the in-app glossary + tooltips from this)

| Term | Meaning |
|---|---|
| AO / Appel d'offres | Tender. AOO = open, AOR = restricted |
| MAPA | Adapted procedure below EU thresholds |
| DCE | Dossier de Consultation des Entreprises — full tender pack |
| RC | Règlement de Consultation — rules of the game (deadlines, criteria, required docs) |
| CCAP / CCTP | Administrative / technical clauses & requirements |
| CCAG | Standard general clauses referenced by CCAP (FCS, PI, TIC, Travaux…) |
| AE / ATTRI1 | Acte d'engagement — the signed offer form |
| BPU / DPGF / DQE | Price schedules (unit prices / global decomposition / estimated quantities) |
| Mémoire technique | The scored technical proposal — the memo BidPilot drafts |
| Candidature vs Offre | Who-you-are file vs what-you-offer file |
| DC1 / DC2 | Standard candidature forms (group letter / capacity declaration) |
| DUME / ESPD | European Single Procurement Document (self-declaration, XML) |
| Profil d'acheteur | Buyer's publication/submission platform (legal term, R2132-3) |
| CPV | Common Procurement Vocabulary — what is bought (8-digit codes) |
| NUTS | EU geo nomenclature — where |
| PIN / Avis de pré-information | Early signal of upcoming tender |
| Avis d'attribution | Award notice — who won, how much |
| DECP | Données Essentielles de la Commande Publique — French open award data |
| JAL | Journal d'Annonces Légales — legal-notice newspaper |
| Soutenance | Oral presentation/defense — where our generated deck is used |
| Variante / PSE | Alternative offer / optional priced services |
| Groupement / Cotraitance | Bidding consortium (solidaire or conjoint) |
| Sous-traitance / DC4 | Subcontracting + its declaration form |
| Standstill | Waiting period between award and signature |
| BOAMP / JOUE / TED | FR national gazette / EU journal / its platform |

---

## Annex D — Regulatory thresholds (CONFIG TABLE — seed `procurement_thresholds`, all `[VERIFY at build]`, revised every 2 years)

| Rule (indicative, 2024–2025 values) | Value |
|---|---|
| EU threshold — works (all buyers) | €5,538,000 HT |
| EU threshold — supplies/services, central government | €143,000 HT |
| EU threshold — supplies/services, sub-central | €221,000 HT |
| EU threshold — utilities supplies/services | €443,000 HT |
| FR — no formal publicity required below | €40,000 HT |
| FR — adapted publicity | €40k–€90k HT |
| FR — BOAMP or JAL mandatory above | €90,000 HT |

Store as dated rows (`valid_from`, `valid_to`); never hardcode; UI shows the threshold context on each tender ("above/below EU threshold").

---

## Annex E — AI prompts, guardrails & evaluation harness

### E.1 Prompt registry layout (`packages/ai/prompts/`)

`extract_admin@v…`, `extract_requirements@v…`, `detect_redflags@v…`, `qualify_match@v…`, `draft_outline@v…`, `draft_section@v…`, `deck_plan@v…`, `qa_suggest@v…`, `classify_evidence@v…` — each: system prompt, JSON schema (from Annex B), few-shot examples from fixtures, model tier, max tokens, temperature.

### E.2 Non-negotiable prompt rules

1. Extraction prompts MUST demand verbatim quotes + page numbers for every item and explicitly allow "not found" (never force an answer).
2. Eliminatory detection runs twice with different framings; union of results, disagreements flagged for review.
3. Generation prompts receive ONLY: extraction JSON + selected evidence chunks + style config. They MUST tag each claim with `[[ev:ID]]` refs; renderer enforces (strip/flag uncited).
4. All prompts FR/EN-agnostic (respond in document language unless org overrides).
5. Prompt changes require eval run in CI (E.4) — a failing gate blocks merge.

### E.3 Fixture corpus (`/fixtures/dce/`)

≥ 15 anonymized-enough real DCEs across IT/training/maintenance × FR/BE/LU × sizes (20–400 pages) × 3 scanned. Each with `gold.json`: labeled admin fields + full requirement register + eliminatory list. Grow with every real-world correction (§9.2). Also `/fixtures/notices/` (48h TED+BOAMP replay) and `/fixtures/decp/` samples.

### E.4 Metrics & gates (CI)

| Metric | Gate |
|---|---|
| Requirement recall (overall) | ≥ 0.90 |
| **Eliminatory recall** | **≥ 0.95** |
| Requirement precision | ≥ 0.85 |
| Admin fields exact-match (dates, weights) | ≥ 0.95 |
| Citation validity (quote found at cited page) | ≥ 0.98 |
| Generation groundedness (sampled claims with valid ev refs) | ≥ 0.95 |
| Cost per 200-page analysis | ≤ target in config |

Harness: `pnpm eval` → runs extraction on corpus, scores vs gold, writes report artifact; nightly full run + on-PR affected run.

---

## Annex F — References (research sources for this spec)

- TED developer docs & Search API: docs.ted.europa.eu (API overview; Search API; "the Search API does not require a key")
- BOAMP open data & API: boamp.fr/pages/donnees-ouvertes-et-api · boamp-datadila.opendatasoft.com (dataset `boamp`) · data.gouv.fr BOAMP API listing · DILA API page
- DECP: guides & consolidated datasets on data.gouv.fr and data.economie.gouv.fr
- Belgium: publicprocurement.be (BOSA e-Procurement) · bosa.belgium.be public-procurement pages
- Luxembourg: pmp.b2g.etat.lu · marches.public.lu
- UK: find-tender.service.gov.uk/Developer/Documentation (OCDS release/record packages)
- US: open.gsa.gov/api/get-opportunities-public-api (SAM.gov)
- Market/competition scans: olra.fr, maitre-ao.fr, dossiersgagnants.fr, remporte.fr, veillao.fr comparison articles (2026); wanao.com; marches-publics.info (AW Solutions)
- OECD: public procurement = 12.7% of GDP (2023), SME administrative-complexity barrier (Government at a Glance / OECD procurement data)
- TED volume: ted.europa.eu statistics pages (~700k+ notices/year)

*End of specification — BidPilot v1.0. Build well.*
