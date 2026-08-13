/**
 * The tender list (SPEC §19 "Tenders", §20.1 "Mes AO ... by stage").
 *
 * This is the other half of two features that would otherwise be invisible: `pursue` on a match
 * creates a tender, and the email connector (§6.5) creates one per link it finds. Both were
 * writing rows nothing could read.
 *
 * Sorted by deadline, nulls last. P3 makes that the only defensible order - the screen's job is
 * to answer "what closes soonest", and a tender with no published deadline cannot outrank one
 * that closes on Friday.
 */

import { Controller, Get, Inject, Req } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request } from "express";

import { withOrgContext } from "@bidpilot/db";

import { TenantService } from "../common/tenant.js";

export type TenderCard = {
  id: string;
  title: string;
  stage: string;
  origin: string;
  deadlineAt: string | null;
  /** Where an email-sourced candidate came from, so a user can open the portal page. */
  sourceUrl: string | null;
  notice: {
    id: string;
    buyerName: string | null;
    cpv: string[];
    amountEst: number | null;
    currency: string | null;
    urls: unknown;
  } | null;
};

export type TenderList = { data: TenderCard[]; countsByStage: Record<string, number> };

@ApiTags("tenders")
@Controller({ path: "tenders", version: "1" })
export class TendersController {
  constructor(@Inject(TenantService) private readonly tenant: TenantService) {}

  @Get()
  @ApiOperation({ summary: "The org's live consultations, soonest deadline first" })
  async list(@Req() request: Request): Promise<TenderList> {
    const principal = await this.tenant.resolve(request);
    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const tenders = await tx.tender.findMany({
        orderBy: [{ deadlineAt: { sort: "asc", nulls: "last" } }, { createdAt: "desc" }],
        include: {
          notice: {
            select: {
              id: true,
              buyerName: true,
              cpv: true,
              amountEst: true,
              currency: true,
              urls: true,
            },
          },
        },
        take: 200,
      });

      const countsByStage: Record<string, number> = {};
      for (const tender of tenders) {
        countsByStage[tender.stage] = (countsByStage[tender.stage] ?? 0) + 1;
      }

      return {
        data: tenders.map((tender) => ({
          id: tender.id,
          title: tender.title,
          stage: tender.stage,
          origin: tender.origin,
          // A tender's own deadline wins when set, since an amendment may have moved it; the
          // notice's is the fallback (§5, P3).
          deadlineAt: (tender.deadlineAt ?? null)?.toISOString() ?? null,
          sourceUrl: readSourceUrl(tender.meta),
          notice: tender.notice
            ? {
                id: tender.notice.id,
                buyerName: tender.notice.buyerName,
                cpv: tender.notice.cpv,
                amountEst: tender.notice.amountEst ? Number(tender.notice.amountEst) : null,
                currency: tender.notice.currency,
                urls: tender.notice.urls,
              }
            : null,
        })),
        countsByStage,
      };
    });
  }
}

/** `meta.source_url`, defensively: `meta` is free-form jsonb written by several code paths. */
function readSourceUrl(meta: unknown): string | null {
  if (!meta || typeof meta !== "object") return null;
  const value = (meta as { source_url?: unknown }).source_url;
  return typeof value === "string" ? value : null;
}
