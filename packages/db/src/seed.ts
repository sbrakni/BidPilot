/**
 * Seed a complete demo environment (SPEC §16 quality gates: "seed script creates a full
 * demo org", Phase 0 exit criteria).
 *
 * The notices come from fixtures/notices/replay_48h.json - a real 48h FR/BE/LU stream from
 * TED and BOAMP. Seeding with real tenders rather than lorem ipsum means the demo shows
 * the product's actual behaviour: real CPV codes, real deadlines, real buyers, and match
 * scores that are computed rather than staged.
 *
 *   pnpm db:seed
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { PrismaClient } from "@prisma/client";

import { id, withOrgContext } from "./index.js";
import {
  CPV_LABELS,
  DOCUMENT_LEAD_TIMES,
  NAF_CPV_MAP,
  NUTS_LABELS,
  SECTOR_PACKS,
  SOURCES,
  THRESHOLDS,
} from "./seed-data.js";

const REPO_ROOT = path.resolve(import.meta.dirname, "../../..");
const REPLAY = path.join(REPO_ROOT, "fixtures", "notices", "replay_48h.json");

type CanonicalNotice = {
  source_refs: Array<{ source: string; external_id: string; url?: string | null }>;
  notice_type: "planning" | "competition" | "result";
  status: "active" | "amended" | "closed" | "awarded" | "cancelled";
  country: string;
  language?: string | null;
  buyer: { name?: string | null; siren?: string | null; siret?: string | null };
  title: string;
  description?: string | null;
  cpv: string[];
  nuts: string[];
  procedure: { type?: string | null };
  lots: unknown[];
  amounts: { estimated_total?: number | null; currency?: string | null };
  dates: {
    published_at?: string | null;
    deadline_at?: string | null;
    questions_deadline_at?: string | null;
    visit?: unknown;
  };
  documents: unknown[];
  urls: Record<string, unknown>;
  requires_account_for_docs?: boolean | null;
  award?: unknown;
  provenance: Record<string, unknown>;
  version: number;
};

/** The three personas from SPEC §3.1, one org each, so every sector pack is demoable. */
const DEMO_ORGS = [
  {
    id: "org_demo_esn",
    name: "Néosys Conseil",
    siren: "812345678",
    pack: "it" as const,
    zones: { nuts: ["FR10"], national: false, max_distance_km: 150 },
    headcount: 35,
    revenues: [
      { year: 2025, amount: 4_200_000 },
      { year: 2024, amount: 3_650_000 },
      { year: 2023, amount: 3_100_000 },
    ],
    capabilityText:
      "ESN de 35 personnes spécialisée en infogérance, développement d'applications métier " +
      "et hébergement souverain. Interventions en Île-de-France pour collectivités et établissements publics.",
    users: [
      { id: "user_lea", email: "lea@neosys.example", name: "Léa Marchand", role: "owner" as const },
      { id: "user_karim", email: "karim@neosys.example", name: "Karim Benali", role: "contributor" as const },
    ],
    certifications: ["ISO 27001"],
  },
  {
    id: "org_demo_formation",
    name: "Atelier Compétences",
    siren: "823456789",
    pack: "training" as const,
    zones: { nuts: [], national: true, max_distance_km: null },
    headcount: 18,
    revenues: [
      { year: 2025, amount: 1_500_000 },
      { year: 2024, amount: 1_280_000 },
      { year: 2023, amount: 1_050_000 },
    ],
    capabilityText:
      "Organisme de formation certifié Qualiopi, catalogue bureautique, management et " +
      "transition numérique. Intervention nationale, présentiel et distanciel.",
    users: [{ id: "user_marc", email: "marc@atelier-competences.example", name: "Marc Dubois", role: "owner" as const }],
    certifications: ["Qualiopi"],
  },
  {
    id: "org_demo_maintenance",
    name: "Provence Facility Services",
    siren: "834567890",
    pack: "maintenance" as const,
    zones: { nuts: ["FRL0", "FRK2"], national: false, max_distance_km: 200 },
    headcount: 80,
    revenues: [
      { year: 2025, amount: 8_400_000 },
      { year: 2024, amount: 7_900_000 },
      { year: 2023, amount: 7_200_000 },
    ],
    capabilityText:
      "Maintenance multitechnique et propreté pour bâtiments publics : CVC, électricité, " +
      "espaces verts, nettoyage. 80 collaborateurs, interventions PACA et Rhône-Alpes.",
    users: [{ id: "user_sofia", email: "sofia@provence-fs.example", name: "Sofia Perrin", role: "owner" as const }],
    certifications: ["ISO 9001", "MASE"],
  },
];

function loadReplayCorpus(): CanonicalNotice[] {
  try {
    const payload = JSON.parse(readFileSync(REPLAY, "utf8")) as { notices: CanonicalNotice[] };
    return payload.notices ?? [];
  } catch (error) {
    console.warn(
      `! could not read ${path.relative(REPO_ROOT, REPLAY)} (${(error as Error).message}).\n` +
        "  Run `python scripts/build_replay_corpus.py` to rebuild it. Seeding without notices.",
    );
    return [];
  }
}

async function seedReferenceData(prisma: PrismaClient): Promise<void> {
  for (const source of SOURCES) {
    await prisma.source.upsert({
      where: { code: source.code },
      update: {
        tier: source.tier,
        kind: source.kind,
        adapter: source.adapter,
        config: source.config,
        schedule: source.schedule,
        legal: source.legal,
        enabled: source.enabled,
      },
      create: {
        id: id("src"),
        code: source.code,
        country: source.country,
        tier: source.tier,
        kind: source.kind,
        adapter: source.adapter,
        config: source.config,
        schedule: source.schedule,
        legal: source.legal,
        enabled: source.enabled,
      },
    });
  }

  for (const threshold of THRESHOLDS) {
    const validFrom = new Date(threshold.validFrom);
    await prisma.procurementThreshold.upsert({
      where: { code_country_validFrom: { code: threshold.code, country: threshold.country, validFrom } },
      update: { amount: threshold.amount, label: threshold.label },
      create: {
        id: id("thr"),
        code: threshold.code,
        country: threshold.country,
        label: threshold.label,
        amount: threshold.amount,
        validFrom,
        sourceNote: "Annex D indicative values - re-verify at each biennial EU revision.",
      },
    });
  }

  for (const lead of DOCUMENT_LEAD_TIMES) {
    await prisma.documentLeadTime.upsert({
      where: { documentCode: lead.documentCode },
      update: { ...lead },
      create: { id: id("dlt"), ...lead },
    });
  }

  for (const label of CPV_LABELS) {
    await prisma.cpvLabel.upsert({
      where: { code_lang: { code: label.code, lang: label.lang } },
      update: { label: label.label },
      create: label,
    });
  }
  for (const label of NUTS_LABELS) {
    await prisma.nutsLabel.upsert({
      where: { code_lang: { code: label.code, lang: label.lang } },
      update: { label: label.label },
      create: label,
    });
  }
  for (const entry of NAF_CPV_MAP) {
    await prisma.nafCpvMap.upsert({ where: { naf: entry.naf }, update: { cpv: entry.cpv }, create: entry });
  }

  console.log(
    `  reference data: ${SOURCES.length} sources, ${THRESHOLDS.length} thresholds, ` +
      `${DOCUMENT_LEAD_TIMES.length} lead times, ${CPV_LABELS.length} CPV labels`,
  );
}

async function seedNotices(prisma: PrismaClient, corpus: CanonicalNotice[]): Promise<Map<string, string>> {
  const byExternalId = new Map<string, string>();
  for (const notice of corpus) {
    const ref = notice.source_refs[0];
    if (!ref) continue;
    // Deterministic id from the source reference so re-seeding updates rather than duplicates.
    const noticeId = `ntc_${ref.source}_${ref.external_id}`.replace(/[^\w]/g, "_").slice(0, 60);
    const data = {
      sourceRefs: notice.source_refs,
      status: notice.status,
      noticeType: notice.notice_type,
      country: notice.country,
      language: notice.language ?? null,
      buyerName: notice.buyer?.name ?? null,
      buyerSiren: notice.buyer?.siren ?? notice.buyer?.siret?.slice(0, 9) ?? null,
      title: notice.title,
      description: notice.description ?? null,
      cpv: notice.cpv ?? [],
      nuts: notice.nuts ?? [],
      procedureType: notice.procedure?.type ?? null,
      amountEst: notice.amounts?.estimated_total ?? null,
      currency: notice.amounts?.currency ?? null,
      publishedAt: notice.dates?.published_at ? new Date(notice.dates.published_at) : null,
      deadlineAt: notice.dates?.deadline_at ? new Date(notice.dates.deadline_at) : null,
      questionsDeadlineAt: notice.dates?.questions_deadline_at
        ? new Date(notice.dates.questions_deadline_at)
        : null,
      lots: notice.lots ?? [],
      urls: notice.urls ?? {},
      requiresAccount: notice.requires_account_for_docs ?? null,
      award: (notice.award as object) ?? null,
      provenance: notice.provenance ?? {},
      version: notice.version ?? 1,
    };
    await prisma.notice.upsert({ where: { id: noticeId }, update: data, create: { id: noticeId, ...data } });
    byExternalId.set(ref.external_id, noticeId);
  }
  console.log(`  notices: ${byExternalId.size} from the 48h replay corpus`);
  return byExternalId;
}

/**
 * Mirror of the Python scoring engine's shape, used only to give the demo inbox
 * plausible, explainable scores. Production scoring lives in
 * services/ingestion/bidpilot_ingestion/matching.py and is the single authority; this is
 * deliberately simple, and says so, rather than pretending to be a second implementation.
 */
function demoScore(
  notice: CanonicalNotice,
  pack: { cpvFamilies: string[]; keywords: readonly string[] },
  zones: { nuts: string[]; national: boolean },
): { score: number; breakdown: object } | null {
  const codes = notice.cpv ?? [];
  const activityHits = codes.filter((code) => pack.cpvFamilies.some((family) => code.startsWith(family)));
  if (activityHits.length === 0) return null;

  const activity = activityHits.length / Math.max(1, codes.length);
  const places = notice.nuts ?? [];
  const geographic = zones.national
    ? 1
    : places.length === 0
      ? 0.5
      : places.some((place) => zones.nuts.some((zone) => place.startsWith(zone) || zone.startsWith(place)))
        ? 0.9
        : 0;
  const deadline = notice.dates?.deadline_at ? new Date(notice.dates.deadline_at) : null;
  const daysLeft = deadline ? (deadline.getTime() - Date.now()) / 86_400_000 : null;
  const comfort = daysLeft === null ? 0.5 : Math.max(0, Math.min(1, daysLeft / 30));

  const factors = [
    { key: "activity_fit", label_fr: "Activité", value: activity, weight: 0.35 },
    { key: "geographic_fit", label_fr: "Géographie", value: geographic, weight: 0.2 },
    { key: "size_fit", label_fr: "Taille", value: 0.5, weight: 0.15 },
    { key: "certification_fit", label_fr: "Certifications", value: 1, weight: 0.1 },
    { key: "buyer_familiarity", label_fr: "Acheteur", value: 0, weight: 0.05 },
    { key: "deadline_comfort", label_fr: "Délai", value: comfort, weight: 0.15 },
  ].map((factor) => ({ ...factor, points: Math.round(factor.value * factor.weight * 100 * 100) / 100 }));

  const total = factors.reduce((sum, factor) => sum + factor.points, 0);
  return { score: Math.round(total), breakdown: { score: Math.round(total), factors, warnings: [] } };
}

async function seedOrgs(prisma: PrismaClient, corpus: CanonicalNotice[], noticeIds: Map<string, string>) {
  for (const org of DEMO_ORGS) {
    const pack = SECTOR_PACKS[org.pack];

    // Each user is created in its own context, with `userId` set: the `users` policies
    // only let a person see and update their own identity row, so the upsert's read step
    // needs that context to be idempotent across re-seeds.
    for (const user of org.users) {
      await withOrgContext(
        { orgId: org.id, userId: user.id },
        (tx) =>
          tx.user.upsert({
            where: { id: user.id },
            update: { name: user.name, email: user.email },
            create: { id: user.id, email: user.email, name: user.name, locale: "fr" },
          }),
        prisma,
      );
    }

    // Org rows are RLS-protected, so creation happens inside the org's own context.
    await withOrgContext(
      org.id,
      async (tx) => {
        await tx.org.upsert({
          where: { id: org.id },
          update: { name: org.name, siren: org.siren },
          create: {
            id: org.id,
            name: org.name,
            siren: org.siren,
            country: "FR",
            locale: "fr",
            tz: "Europe/Paris",
            plan: "trial",
            aiCreditsBalance: 100_000,
          },
        });

        for (const user of org.users) {
          await tx.orgMember.upsert({
            where: { orgId_userId: { orgId: org.id, userId: user.id } },
            update: { role: user.role },
            create: { orgId: org.id, userId: user.id, role: user.role },
          });
        }

        await tx.companyProfile.upsert({
          where: { orgId: org.id },
          update: {
            headcount: org.headcount,
            revenues: org.revenues,
            zones: org.zones,
            cpvFamilies: [...pack.cpvFamilies],
            keywords: [...pack.keywords],
            capabilityText: org.capabilityText,
          },
          create: {
            orgId: org.id,
            identity: { legal_name: org.name, siren: org.siren, sector_pack: org.pack },
            headcount: org.headcount,
            revenues: org.revenues,
            zones: org.zones,
            cpvFamilies: [...pack.cpvFamilies],
            keywords: [...pack.keywords],
            negativeKeywords: [],
            capabilityText: org.capabilityText,
          },
        });

        const watchId = `wp_${org.id}`;
        await tx.watchProfile.upsert({
          where: { id: watchId },
          update: {},
          create: {
            id: watchId,
            orgId: org.id,
            name: `${pack.labelFr} - ${org.zones.national ? "national" : org.zones.nuts.join(", ")}`,
            filters: {
              cpv_families: pack.cpvFamilies,
              countries: ["FR"],
              nuts: org.zones.nuts,
              notice_types: ["competition"],
              keywords_include: [],
              keywords_exclude: [],
            },
            alertPolicy: { digest: "daily", instant_min_score: 80 },
          },
        });

        // A starter evidence vault so the vault-health widget and eligibility checks have
        // something real to reason about (§7.2). Expiry dates are deliberately mixed.
        const today = new Date();
        const inDays = (days: number) => new Date(today.getTime() + days * 86_400_000);
        const evidences = [
          { code: "kbis", title: "Extrait KBIS", kind: "admin" as const, expiresAt: inDays(60) },
          { code: "urssaf", title: "Attestation de vigilance URSSAF", kind: "admin" as const, expiresAt: inDays(21) },
          { code: "fiscale", title: "Attestation fiscale", kind: "admin" as const, expiresAt: inDays(-5) },
          ...org.certifications.map((name) => ({
            code: name.toLowerCase().replace(/\s+/g, "-"),
            title: `Certification ${name}`,
            kind: "certification" as const,
            expiresAt: inDays(400),
          })),
        ];
        for (const evidence of evidences) {
          const evidenceId = `ev_${org.id}_${evidence.code}`;
          const expired = evidence.expiresAt.getTime() < today.getTime();
          const expiring = !expired && evidence.expiresAt.getTime() < inDays(30).getTime();
          await tx.evidence.upsert({
            where: { id: evidenceId },
            update: { expiresAt: evidence.expiresAt, status: expired ? "expired" : expiring ? "expiring" : "valid" },
            create: {
              id: evidenceId,
              orgId: org.id,
              kind: evidence.kind,
              title: evidence.title,
              fileKeys: [`orgs/${org.id}/evidences/${evidence.code}.pdf`],
              expiresAt: evidence.expiresAt,
              status: expired ? "expired" : expiring ? "expiring" : "valid",
              tags: [],
            },
          });
        }

        const referenceId = `ref_${org.id}_1`;
        await tx.referenceProject.upsert({
          where: { id: referenceId },
          update: {},
          create: {
            id: referenceId,
            orgId: org.id,
            client: "Conseil départemental de démonstration",
            clientType: "public",
            title: `Prestation de référence - ${pack.labelFr}`,
            description: `Marché pluriannuel exécuté pour un acheteur public dans le domaine ${pack.labelFr.toLowerCase()}.`,
            cpv: [...pack.cpvFamilies].map((family) => family.padEnd(8, "0")),
            amount: 320_000,
            periodStart: new Date("2023-01-01"),
            periodEnd: new Date("2025-12-31"),
            location: org.zones.nuts[0] ?? "FR",
            evidenceIds: [],
            blocks: {},
          },
        });

        // Matches, computed rather than staged, so the demo inbox behaves like production.
        let created = 0;
        for (const notice of corpus) {
          if (notice.notice_type !== "competition" || notice.country !== "FR") continue;
          const noticeId = noticeIds.get(notice.source_refs[0]?.external_id ?? "");
          if (!noticeId) continue;
          const scored = demoScore(notice, pack, org.zones);
          if (!scored) continue;
          const matchId = `mch_${org.id}_${noticeId}`.slice(0, 80);
          await tx.match.upsert({
            where: { orgId_noticeId: { orgId: org.id, noticeId } },
            update: { score: scored.score, breakdown: scored.breakdown },
            create: {
              id: matchId,
              orgId: org.id,
              noticeId,
              watchProfileId: watchId,
              score: scored.score,
              breakdown: scored.breakdown,
              state: "new",
            },
          });
          created += 1;
        }
        console.log(`  ${org.name}: ${org.users.length} user(s), ${evidences.length} evidences, ${created} matches`);
      },
      prisma,
    );
  }
}

async function main(): Promise<void> {
  // Seeding writes the shared market data in §18.1, which the application role is
  // deliberately denied. So the seeder connects as the owner - it is admin tooling, and
  // the fact that DATABASE_APP_URL cannot do this is the isolation working, not a bug.
  const ownerUrl = process.env.DATABASE_URL;
  if (!ownerUrl) {
    throw new Error("DATABASE_URL (owner connection) must be set to seed");
  }
  const prisma = new PrismaClient({ datasources: { db: { url: ownerUrl } } });
  console.log("seeding BidPilot demo environment …");
  await seedReferenceData(prisma);
  const corpus = loadReplayCorpus();
  const noticeIds = await seedNotices(prisma, corpus);
  await seedOrgs(prisma, corpus, noticeIds);
  console.log("done. Sign in as lea@neosys.example to see the IT inbox.");
  await prisma.$disconnect();
}

main().catch(async (error) => {
  console.error(error);
  process.exitCode = 1;
});
