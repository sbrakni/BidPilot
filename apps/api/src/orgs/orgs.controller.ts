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

/** `sources+{org}@{domain}` (SPEC §6.5), or null where the connector is not configured. */
export function inboundAddress(orgId: string): string | null {
  const domain = process.env.INBOUND_EMAIL_DOMAIN;
  return domain ? `sources+${orgId}@${domain}` : null;
}

/** Declared for the same reasons as the match shapes: a written-down wire contract. */
export type OrgSummary = {
  id: string;
  name: string;
  siren: string | null;
  locale: string;
  tz: string;
  plan: string;
  role: string;
  /**
   * The address this org subscribes to portal alert emails (SPEC §6.5).
   *
   * Derived rather than stored: it is a pure function of the org id and the deployment's inbound
   * domain, so there is no second copy to drift, and rotating the domain does not need a
   * migration. Null when no inbound domain is configured, which is how the UI knows to say the
   * connector is unavailable instead of showing an address that goes nowhere.
   */
  inboundEmail: string | null;
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
        inboundEmail: inboundAddress(org.id),
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
