/**
 * Public coverage status (SPEC §6.9).
 *
 * The spec makes this a MUST: *"Coverage transparency (MUST): public status page listing
 * connected sources + per-source freshness - turns the moat into visible trust."*
 *
 * Which is the point. Claiming exhaustive coverage is worth nothing; showing which sources are
 * connected, when each last succeeded, and which are degraded is worth something - and it also
 * commits us publicly to noticing when a source breaks.
 *
 * Deliberately unauthenticated, and deliberately limited to operational facts. It exposes no
 * notice content and no tenant data, so it is safe to serve to anyone.
 */

import { Controller, Get } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";

import { getPrisma } from "@bidpilot/db";

/** Silence budgets mirroring services/ingestion/.../health.py (SPEC §6.1). */
const SILENCE_BUDGET_HOURS: Record<string, number> = {
  official_api: 6,
  platform: 24,
  long_tail: 72,
  signals: 72,
};

export type SourceStatus = {
  code: string;
  country: string;
  tier: string;
  kind: string;
  health: string;
  enabled: boolean;
  legalBasis: string | null;
  lastSuccessAt: string | null;
  hoursSinceSuccess: number | null;
  withinFreshnessBudget: boolean | null;
  notices7d: number;
};

export type CoverageStatus = {
  generatedAt: string;
  summary: { total: number; enabled: number; green: number; degraded: number; silent: number };
  /** Gaps we know about, stated rather than left for a user to discover (SPEC §6.5). */
  knownGaps: string[];
  sources: SourceStatus[];
};

@ApiTags("status")
@Controller({ path: "status", version: "1" })
export class SourcesController {
  @Get("coverage")
  @ApiOperation({ summary: "Public source coverage and freshness (SPEC §6.9)" })
  async coverage(): Promise<CoverageStatus> {
    const rows = await getPrisma().source.findMany({
      orderBy: [{ tier: "asc" }, { code: "asc" }],
      select: {
        code: true,
        country: true,
        tier: true,
        kind: true,
        health: true,
        enabled: true,
        legal: true,
        lastSuccessAt: true,
        notices7d: true,
      },
    });

    const now = Date.now();
    const sources: SourceStatus[] = rows.map((row) => {
      const hours = row.lastSuccessAt ? (now - row.lastSuccessAt.getTime()) / 3_600_000 : null;
      const budget = SILENCE_BUDGET_HOURS[row.tier] ?? 24;
      return {
        code: row.code,
        country: row.country,
        tier: row.tier,
        kind: row.kind,
        health: row.health,
        enabled: row.enabled,
        // The recorded legal basis is public on purpose: §16's scraping posture is a
        // commitment, and publishing it invites the correction if we get one wrong.
        legalBasis: (row.legal as { basis?: string } | null)?.basis ?? null,
        lastSuccessAt: row.lastSuccessAt?.toISOString() ?? null,
        hoursSinceSuccess: hours === null ? null : Math.round(hours * 10) / 10,
        withinFreshnessBudget: hours === null ? null : hours <= budget,
        notices7d: row.notices7d,
      };
    });

    return {
      generatedAt: new Date().toISOString(),
      summary: {
        total: sources.length,
        enabled: sources.filter((s) => s.enabled).length,
        green: sources.filter((s) => s.health === "green").length,
        degraded: sources.filter((s) => s.health === "degraded").length,
        silent: sources.filter((s) => s.health === "silent").length,
      },
      knownGaps: [
        // §6.5 documents the JAL gap and requires it be "shown transparently in coverage
        // stats". A gap a user discovers on their own costs far more trust than one we state.
        "JAL (legal-notice newspapers), covering French adapted-publicity notices between " +
          "€40k and €90k, is not yet ingested. Deferred to Phase 4 (SPEC §6.5).",
        "Sources pending legal review are listed as disabled and are not fetched (SPEC §24.7).",
      ],
      sources,
    };
  }
}
