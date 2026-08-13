/**
 * Company profile endpoints (SPEC §19 "Profile", §7.1, §15.3).
 *
 * `POST /v1/profile/bootstrap` is the aha moment of §15.3: a SIREN in, and the profile - identity,
 * geography, suggested CPV families - comes back pre-filled, so the user confirms rather than
 * types. That is what makes P5's "value in under five minutes" achievable.
 *
 * Two rules the bootstrap follows carefully:
 *   * **Suggestions are suggestions.** CPV families derived from the NAF code are returned as
 *     `suggested`, and only become the profile when the user saves. Silently adopting them would
 *     make the matching engine act on data the user never confirmed (P6).
 *   * **Absent means absent** (P1). The registry gives an employee *band*, not a headcount, so no
 *     headcount is written - the band and its range are surfaced for the user to confirm.
 */

import {
  BadRequestException,
  Body,
  Controller,
  Get,
  Inject,
  Post,
  Put,
  Req,
  ServiceUnavailableException,
} from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request } from "express";

import { id, withGlobalContext, withOrgContext } from "@bidpilot/db";

import { TenantService } from "../common/tenant.js";
import { EntrepriseLookupError, lookupCompany, type CompanyIdentity } from "./entreprise.js";

export type ProfileZones = { nuts: string[]; national: boolean; max_distance_km: number | null };

export type ProfileResponse = {
  orgId: string;
  identity: Record<string, unknown>;
  revenues: Array<{ year: number; amount: number }>;
  headcount: number | null;
  zones: ProfileZones;
  cpvFamilies: string[];
  keywords: string[];
  negativeKeywords: string[];
  capabilityText: string | null;
};

export type BootstrapResponse = {
  identity: CompanyIdentity;
  /** Not applied until the user saves - see the note above about P6. */
  suggested: { cpvFamilies: string[]; zones: ProfileZones };
};

const EMPTY_ZONES: ProfileZones = { nuts: [], national: false, max_distance_km: null };

@ApiTags("profile")
@Controller({ path: "profile", version: "1" })
export class ProfileController {
  constructor(@Inject(TenantService) private readonly tenant: TenantService) {}

  @Get()
  async get(@Req() request: Request): Promise<ProfileResponse> {
    const principal = await this.tenant.resolve(request);
    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const profile = await tx.companyProfile.findUnique({ where: { orgId: principal.orgId } });
      return {
        orgId: principal.orgId,
        identity: (profile?.identity as Record<string, unknown>) ?? {},
        revenues: (profile?.revenues as Array<{ year: number; amount: number }>) ?? [],
        headcount: profile?.headcount ?? null,
        zones: (profile?.zones as ProfileZones) ?? EMPTY_ZONES,
        cpvFamilies: profile?.cpvFamilies ?? [],
        keywords: profile?.keywords ?? [],
        negativeKeywords: profile?.negativeKeywords ?? [],
        capabilityText: profile?.capabilityText ?? null,
      };
    });
  }

  @Post("bootstrap")
  @ApiOperation({ summary: "Pre-fill the profile from a SIREN/SIRET (SPEC §7.1, §15.3)" })
  async bootstrap(
    @Req() request: Request,
    @Body() body: { siret?: string; siren?: string },
  ): Promise<BootstrapResponse> {
    const principal = await this.tenant.resolve(request);
    const identifier = (body?.siret ?? body?.siren ?? "").trim();
    if (!identifier) {
      throw new BadRequestException("siret or siren is required");
    }

    let identity: CompanyIdentity;
    try {
      identity = await lookupCompany(identifier);
    } catch (error) {
      if (error instanceof EntrepriseLookupError) {
        if (error.reason === "invalid_input") throw new BadRequestException(error.message);
        if (error.reason === "not_found") throw new BadRequestException(error.message);
        // The registry being slow or down must degrade to manual entry, not dead-end onboarding.
        throw new ServiceUnavailableException(
          "the company registry is unavailable; you can enter your details manually",
        );
      }
      throw error;
    }

    // NAF → CPV suggestions come from seeded reference data (§7.1), which is global market data
    // and so is read without tenant context.
    const suggestedCpv = identity.naf
      ? await withGlobalContext(async (tx) => {
          const mapping = await tx.nafCpvMap.findUnique({ where: { naf: identity.naf as string } });
          return mapping?.cpv ?? [];
        })
      : [];

    const zones: ProfileZones = identity.address.nuts
      ? { nuts: [identity.address.nuts], national: false, max_distance_km: null }
      : EMPTY_ZONES;

    // The identity *is* recorded now - it is confirmed fact from an official registry, and having
    // it lets the user leave and come back. The suggestions are not.
    await withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      await tx.companyProfile.upsert({
        where: { orgId: principal.orgId },
        update: { identity: identity as unknown as object },
        create: {
          orgId: principal.orgId,
          identity: identity as unknown as object,
          zones: EMPTY_ZONES as unknown as object,
          cpvFamilies: [],
          keywords: [],
          negativeKeywords: [],
        },
      });
      await tx.org.update({
        where: { id: principal.orgId },
        data: { name: identity.legalName, siren: identity.siren },
      });
      await tx.event.create({
        data: {
          id: id("evt"),
          orgId: principal.orgId,
          actor: principal.userId,
          kind: "profile.bootstrapped",
          entity: principal.orgId,
          payload: { siren: identity.siren },
        },
      });
    });

    return { identity, suggested: { cpvFamilies: suggestedCpv, zones } };
  }

  @Put()
  @ApiOperation({ summary: "Save the confirmed profile and refresh matching inputs" })
  async update(
    @Req() request: Request,
    @Body()
    body: {
      cpvFamilies?: string[];
      keywords?: string[];
      negativeKeywords?: string[];
      zones?: ProfileZones;
      headcount?: number | null;
      revenues?: Array<{ year: number; amount: number }>;
      capabilityText?: string | null;
    },
  ): Promise<ProfileResponse> {
    const principal = await this.tenant.resolve(request);

    // CPV families are prefixes, so 2-8 digits are all valid ("72", "7221", "72212000").
    const cpvFamilies = (body.cpvFamilies ?? []).map((code) => code.replace(/\D/g, "")).filter(Boolean);
    if (cpvFamilies.some((code) => code.length < 2 || code.length > 8)) {
      throw new BadRequestException("a CPV family must be 2 to 8 digits");
    }
    const revenues = body.revenues ?? [];
    if (revenues.some((entry) => !Number.isFinite(entry.amount) || entry.amount < 0)) {
      throw new BadRequestException("revenue amounts must be positive numbers");
    }

    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const saved = await tx.companyProfile.upsert({
        where: { orgId: principal.orgId },
        update: {
          ...(body.cpvFamilies ? { cpvFamilies } : {}),
          ...(body.keywords ? { keywords: body.keywords } : {}),
          ...(body.negativeKeywords ? { negativeKeywords: body.negativeKeywords } : {}),
          ...(body.zones ? { zones: body.zones as unknown as object } : {}),
          ...(body.headcount !== undefined ? { headcount: body.headcount } : {}),
          ...(body.revenues ? { revenues: revenues as unknown as object } : {}),
          ...(body.capabilityText !== undefined ? { capabilityText: body.capabilityText } : {}),
        },
        create: {
          orgId: principal.orgId,
          identity: {},
          cpvFamilies,
          keywords: body.keywords ?? [],
          negativeKeywords: body.negativeKeywords ?? [],
          zones: (body.zones ?? EMPTY_ZONES) as unknown as object,
          headcount: body.headcount ?? null,
          revenues: revenues as unknown as object,
          capabilityText: body.capabilityText ?? null,
        },
      });

      // A watch profile is what actually produces matches (§8.1), so confirming a profile creates
      // one if the org has none - otherwise onboarding would end with an empty inbox and no
      // indication why.
      const existingWatch = await tx.watchProfile.count();
      if (existingWatch === 0 && cpvFamilies.length > 0) {
        await tx.watchProfile.create({
          data: {
            id: id("wp"),
            orgId: principal.orgId,
            name: "Veille principale",
            filters: {
              cpv_families: cpvFamilies,
              countries: ["FR"],
              nuts: body.zones?.national ? [] : (body.zones?.nuts ?? []),
              notice_types: ["competition"],
            },
            alertPolicy: { digest: "daily", instant_min_score: 80 },
          },
        });
      }

      return {
        orgId: principal.orgId,
        identity: (saved.identity as Record<string, unknown>) ?? {},
        revenues: (saved.revenues as Array<{ year: number; amount: number }>) ?? [],
        headcount: saved.headcount ?? null,
        zones: (saved.zones as ProfileZones) ?? EMPTY_ZONES,
        cpvFamilies: saved.cpvFamilies,
        keywords: saved.keywords,
        negativeKeywords: saved.negativeKeywords,
        capabilityText: saved.capabilityText ?? null,
      };
    });
  }
}
