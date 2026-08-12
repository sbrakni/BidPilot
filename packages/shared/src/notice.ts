/**
 * The canonical notice, in TypeScript (SPEC Annex B.1).
 *
 * There are three representations of this one contract, and that is a deliberate risk:
 *
 *   1. `packages/shared/schemas/notice.v1.json` - JSON Schema, the cross-language authority
 *   2. this Zod schema - what the API and web app validate and infer types from
 *   3. `services/ingestion/bidpilot_ingestion/canonical.py` - pydantic, what adapters emit
 *
 * Three copies of a contract drift unless something forces them together, so
 * `notice.test.ts` validates the *real* Python-produced replay corpus against both (1) and
 * (2). If the Python side starts emitting a shape TypeScript rejects, that test fails.
 */

import { z } from "zod";

export const SCHEMA_VERSION = 1;

export const noticeTypes = ["planning", "competition", "result"] as const;
export const noticeStatuses = ["active", "amended", "closed", "awarded", "cancelled"] as const;
export const procedureTypes = [
  "open",
  "restricted",
  "negotiated",
  "competitive-dialogue",
  "innovation-partnership",
  "direct-award",
  "adapted",
  "other",
] as const;
export const contractNatures = ["works", "supplies", "services"] as const;
export const documentKinds = [
  "rc",
  "ccap",
  "cctp",
  "ae",
  "price",
  "dume",
  "annex",
  "amendment",
  "other",
] as const;

export type NoticeType = (typeof noticeTypes)[number];
export type NoticeStatus = (typeof noticeStatuses)[number];
export type DocumentKind = (typeof documentKinds)[number];

/** Nullable-and-optional: absent and explicitly-null both mean "the source did not say". */
const maybe = <T extends z.ZodTypeAny>(schema: T) => schema.nullish();

export const sourceRefSchema = z.object({
  source: z.string().min(1),
  external_id: z.string().min(1),
  url: maybe(z.string()),
  fetched_at: maybe(z.string()),
});

export const buyerSchema = z.object({
  name: maybe(z.string()),
  siren: maybe(z.string().regex(/^\d{9}$/)),
  siret: maybe(z.string().regex(/^\d{14}$/)),
  national_id: maybe(z.string()),
  contact: z
    .object({
      email: maybe(z.string()),
      phone: maybe(z.string()),
      website: maybe(z.string()),
    })
    .default({}),
  address: z
    .object({
      street: maybe(z.string()),
      postcode: maybe(z.string()),
      city: maybe(z.string()),
      nuts: maybe(z.string()),
      country: maybe(z.string()),
    })
    .default({}),
});

export const lotSchema = z.object({
  lot_id: z.string().min(1),
  title: maybe(z.string()),
  description: maybe(z.string()),
  cpv: z.array(z.string()).default([]),
  amount_est: maybe(z.number()),
  currency: maybe(z.string()),
  place_nuts: z.array(z.string()).default([]),
  deadline_at: maybe(z.string()),
});

export const datesSchema = z.object({
  published_at: maybe(z.string()),
  deadline_at: maybe(z.string()),
  questions_deadline_at: maybe(z.string()),
  visit: z
    .object({ mandatory: maybe(z.boolean()), dates: z.array(z.string()).default([]) })
    .default({}),
  /** IANA zone. A submission deadline is defined in the buyer's clock (SPEC §5). */
  tz: maybe(z.string()),
});

export const canonicalNoticeSchema = z.object({
  schema_version: z.literal(SCHEMA_VERSION),
  uid: maybe(z.string()),
  cluster_id: maybe(z.string()),
  source_refs: z.array(sourceRefSchema).min(1),
  notice_type: z.enum(noticeTypes),
  status: z.enum(noticeStatuses).default("active"),
  country: z.string().regex(/^[A-Z]{2}$/),
  language: maybe(z.string().regex(/^[a-z]{2}$/)),
  buyer: buyerSchema.default({}),
  title: z.string().min(1),
  description: maybe(z.string()),
  /** 8-digit CPV codes, deduplicated. Never translated (SPEC §5). */
  cpv: z.array(z.string().regex(/^\d{8}$/)).default([]),
  nuts: z.array(z.string().regex(/^[A-Z]{2}[0-9A-Z]{0,3}$/)).default([]),
  procedure: z
    .object({
      type: maybe(z.enum(procedureTypes)),
      national_label: maybe(z.string()),
      framework: maybe(z.boolean()),
      reserved_sme: maybe(z.boolean()),
      contract_nature: z.array(z.enum(contractNatures)).default([]),
    })
    .default({}),
  lots: z.array(lotSchema).default([]),
  amounts: z
    .object({
      estimated_total: maybe(z.number()),
      currency: maybe(z.string().regex(/^[A-Z]{3}$/)),
      vat: maybe(z.enum(["HT", "TTC"])),
    })
    .default({}),
  dates: datesSchema.default({}),
  documents: z
    .array(
      z.object({
        kind: z.enum(documentKinds).default("other"),
        title: maybe(z.string()),
        url: maybe(z.string()),
        file_key: maybe(z.string()),
        pages: maybe(z.number().int()),
      }),
    )
    .default([]),
  urls: z
    .object({
      notice: maybe(z.string()),
      documents: maybe(z.string()),
      submission: maybe(z.string()),
    })
    .default({}),
  requires_account_for_docs: maybe(z.boolean()),
  award: maybe(
    z.object({
      supplier_name: maybe(z.string()),
      supplier_siren: maybe(z.string().regex(/^\d{9}$/)),
      amount: maybe(z.number()),
      currency: maybe(z.string()),
      signed_at: maybe(z.string()),
      duration_months: maybe(z.number().int()),
    }),
  ),
  provenance: z.object({
    /** `name@semver`, e.g. `boamp@1.0.0`. */
    adapter: z.string().min(1),
    normalized_at: z.string(),
    raw_content_hash: maybe(z.string()),
    /** Source keys an adapter saw but did not map - the source-changed-shape signal. */
    unmapped_fields: z.array(z.string()).default([]),
  }),
  version: z.number().int().min(1),
});

export type CanonicalNotice = z.infer<typeof canonicalNoticeSchema>;

/** Match score breakdown (SPEC §8.2). The factors must sum to the score - P4. */
export const matchScoreSchema = z.object({
  score: z.number().int().min(0).max(100),
  factors: z
    .array(
      z.object({
        key: z.string(),
        label_fr: z.string(),
        value: z.number(),
        weight: z.number(),
        points: z.number(),
        detail: z.string().default(""),
      }),
    )
    .min(1),
  warnings: z.array(z.string()).default([]),
});

export type MatchScore = z.infer<typeof matchScoreSchema>;

/**
 * Check the invariant §8.4 requires the UI to be able to show: the rendered factor points
 * add up to the rendered score. Tolerance covers display rounding only.
 */
export function factorsSumToScore(score: MatchScore, tolerance = 0.51): boolean {
  const total = score.factors.reduce((sum, factor) => sum + factor.points, 0);
  return Math.abs(total - score.score) <= tolerance;
}
