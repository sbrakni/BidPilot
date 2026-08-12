/**
 * Cross-language contract conformance.
 *
 * The canonical notice exists in three places (JSON Schema, Zod, pydantic). This suite
 * runs the *real* Python-produced replay corpus through the JSON Schema and the Zod schema
 * and requires both to accept all ~1,400 notices. If the ingestion service starts emitting
 * a shape TypeScript rejects - a renamed field, a widened enum, a null where a string was
 * promised - it fails here rather than at runtime in the inbox.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import {
  canonicalNoticeSchema,
  deadlineUrgency,
  factorsSumToScore,
  formatAmount,
  matchScoreSchema,
  type CanonicalNotice,
} from "./index.js";

const REPO_ROOT = path.resolve(import.meta.dirname, "../../..");
const SCHEMA_PATH = path.join(REPO_ROOT, "packages", "shared", "schemas", "notice.v1.json");
const CORPUS_PATH = path.join(REPO_ROOT, "fixtures", "notices", "replay_48h.json");

const jsonSchema = JSON.parse(readFileSync(SCHEMA_PATH, "utf8"));
const corpus = JSON.parse(readFileSync(CORPUS_PATH, "utf8")) as { notices: unknown[] };

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validateJsonSchema = ajv.compile(jsonSchema);

describe("canonical notice contract", () => {
  it("has a corpus to validate against", () => {
    expect(corpus.notices.length).toBeGreaterThan(100);
  });

  it("accepts every notice the Python adapters produced (JSON Schema)", () => {
    const failures: Array<{ index: number; errors: string }> = [];
    corpus.notices.forEach((notice, index) => {
      if (!validateJsonSchema(notice)) {
        failures.push({
          index,
          errors: (validateJsonSchema.errors ?? [])
            .map((error) => `${error.instancePath} ${error.message}`)
            .join("; "),
        });
      }
    });
    expect(failures.slice(0, 5)).toEqual([]);
  });

  it("accepts every notice the Python adapters produced (Zod)", () => {
    const failures: Array<{ index: number; issue: string }> = [];
    corpus.notices.forEach((notice, index) => {
      const result = canonicalNoticeSchema.safeParse(notice);
      if (!result.success) {
        failures.push({ index, issue: result.error.issues[0]?.message ?? "unknown" });
      }
    });
    expect(failures.slice(0, 5)).toEqual([]);
  });

  it("preserves the invariants the rest of the product relies on", () => {
    const notices = corpus.notices.map((entry) => canonicalNoticeSchema.parse(entry));
    for (const notice of notices) {
      // Provenance and a source reference are what make a notice auditable (§6.10).
      expect(notice.source_refs.length).toBeGreaterThan(0);
      expect(notice.provenance.adapter).toContain("@");
      // Deduplicated CPV: a repeated code would double-count in activity scoring.
      expect(new Set(notice.cpv).size).toBe(notice.cpv.length);
    }
  });

  it("rejects a notice with no source reference", () => {
    const valid = canonicalNoticeSchema.parse(corpus.notices[0]);
    const orphan: CanonicalNotice = { ...valid, source_refs: [] };
    expect(canonicalNoticeSchema.safeParse(orphan).success).toBe(false);
    expect(validateJsonSchema(orphan)).toBe(false);
  });

  it("rejects an invented CPV code shape", () => {
    const valid = canonicalNoticeSchema.parse(corpus.notices[0]);
    expect(canonicalNoticeSchema.safeParse({ ...valid, cpv: ["72"] }).success).toBe(false);
  });

  it("rejects an ISO-3166 alpha-3 country code", () => {
    // TED mixes alpha-3 country codes into NUTS arrays; the contract must not let one
    // through as a country, or geographic filtering silently breaks.
    const valid = canonicalNoticeSchema.parse(corpus.notices[0]);
    expect(canonicalNoticeSchema.safeParse({ ...valid, country: "FRA" }).success).toBe(false);
  });
});

describe("match score contract", () => {
  it("requires the factors to sum to the score (SPEC §8.4)", () => {
    const honest = matchScoreSchema.parse({
      score: 86,
      factors: [
        { key: "activity_fit", label_fr: "Activité", value: 1, weight: 0.35, points: 35, detail: "CPV 72" },
        { key: "geographic_fit", label_fr: "Géographie", value: 0.9, weight: 0.2, points: 18, detail: "FR10" },
        { key: "size_fit", label_fr: "Taille", value: 0.5, weight: 0.15, points: 7.5, detail: "-" },
        { key: "certification_fit", label_fr: "Certifications", value: 1, weight: 0.1, points: 10, detail: "-" },
        { key: "buyer_familiarity", label_fr: "Acheteur", value: 0, weight: 0.05, points: 0, detail: "-" },
        { key: "deadline_comfort", label_fr: "Délai", value: 1, weight: 0.15, points: 15, detail: "32 j" },
      ],
      warnings: [],
    });
    expect(factorsSumToScore(honest)).toBe(true);

    // A score that does not match its own breakdown is exactly what P4 forbids.
    expect(factorsSumToScore({ ...honest, score: 99 })).toBe(false);
  });
});

describe("display helpers (SPEC §20.3, §20.4)", () => {
  const now = new Date("2026-08-13T00:00:00Z");

  it("bands deadline urgency", () => {
    expect(deadlineUrgency("2026-08-13T12:00:00Z", now)).toBe("critical");
    expect(deadlineUrgency("2026-08-15T12:00:00Z", now)).toBe("urgent");
    expect(deadlineUrgency("2026-08-19T00:00:00Z", now)).toBe("soon");
    expect(deadlineUrgency("2026-09-30T00:00:00Z", now)).toBe("comfortable");
    expect(deadlineUrgency("2026-08-01T00:00:00Z", now)).toBe("passed");
    expect(deadlineUrgency(null, now)).toBe("none");
  });

  it("formats amounts the way practitioners write them", () => {
    // Non-breaking spaces are what Intl emits for fr-FR grouping.
    expect(formatAmount(1_250_000, "EUR", "HT")?.replace(/ | /g, " ")).toBe("1 250 000 € HT");
    // No VAT basis stated: none asserted. Claiming HT on an unknown basis misstates the
    // price by 20%.
    expect(formatAmount(1000, "EUR", null)).not.toContain("HT");
    expect(formatAmount(null)).toBeNull();
  });
});
