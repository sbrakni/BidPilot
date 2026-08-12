/**
 * Org and profile endpoints (SPEC §19 "Orgs", "Profile").
 *
 * Everything here reads through the tenant context, so an org can only ever describe
 * itself - there is no code path that takes an org id from the caller.
 */

import { Controller, Get, Inject, Req } from "@nestjs/common";
import { ApiTags } from "@nestjs/swagger";
import type { Request } from "express";

import { withOrgContext } from "@bidpilot/db";

import { TenantService } from "../common/tenant.js";

/** Declared for the same reasons as the match shapes: a written-down wire contract. */
export type OrgSummary = {
  id: string;
  name: string;
  siren: string | null;
  locale: string;
  tz: string;
  plan: string;
  role: string;
  profile: {
    headcount: number | null;
    cpvFamilies: string[];
    keywords: string[];
    zones: unknown;
  } | null;
  counters: { newMatches: number; evidenceNeedingAttention: number };
};

@ApiTags("orgs")
@Controller({ path: "org", version: "1" })
export class OrgsController {
  // Explicit token: esbuild cannot emit `design:paramtypes`, so implicit
  // constructor injection would resolve to undefined under vitest.
  constructor(@Inject(TenantService) private readonly tenant: TenantService) {}

  @Get()
  async current(@Req() request: Request): Promise<OrgSummary> {
    const principal = await this.tenant.resolve(request);
    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const org = await tx.org.findFirstOrThrow({ include: { profile: true } });
      const [matchCount, expiring] = await Promise.all([
        tx.match.count({ where: { state: "new" } }),
        // Vault health (§7.2): expired and expiring are what the dashboard widget counts.
        tx.evidence.count({ where: { status: { in: ["expiring", "expired"] } } }),
      ]);
      return {
        id: org.id,
        name: org.name,
        siren: org.siren,
        locale: org.locale,
        tz: org.tz,
        plan: org.plan,
        role: principal.role,
        profile: org.profile
          ? {
              headcount: org.profile.headcount,
              cpvFamilies: org.profile.cpvFamilies,
              keywords: org.profile.keywords,
              zones: org.profile.zones,
            }
          : null,
        counters: { newMatches: matchCount, evidenceNeedingAttention: expiring },
      };
    });
  }
}
