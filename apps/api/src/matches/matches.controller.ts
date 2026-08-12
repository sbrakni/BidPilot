/**
 * Match inbox endpoints (SPEC §19 "Matches", §8.3).
 *
 * States are `new → shortlisted | dismissed(reason) | pursued`. Dismissal *reasons* are
 * recorded rather than discarded: they are the training signal for the weight-tuning loop
 * in §8.3, and the reason a dismissed notice stays out of future digests.
 */

import {
  BadRequestException,
  Body,
  Controller,
  Get,
  Inject,
  NotFoundException,
  Param,
  Post,
  Query,
  Req,
} from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request } from "express";

import { getPrisma, id, withOrgContext, type MatchState } from "@bidpilot/db";

import { TenantService } from "../common/tenant.js";

/** Dismissal reasons from §8.3. A closed list, because free text cannot tune weights. */
const DISMISS_REASONS = ["too_big", "too_small", "wrong_activity", "no_time", "bad_buyer", "other"] as const;
type DismissReason = (typeof DISMISS_REASONS)[number];

const MAX_PAGE_SIZE = 100;

/**
 * Response shapes are declared rather than inferred.
 *
 * Practically, Prisma's inferred types cannot be named across a pnpm workspace boundary.
 * Usefully, it also means the wire contract is written down here and in the generated
 * OpenAPI document, instead of being whatever the ORM happened to return.
 */
export type NoticeSummary = {
  id: string;
  title: string;
  buyerName: string | null;
  country: string;
  cpv: string[];
  nuts: string[];
  amountEst: number | null;
  currency: string | null;
  deadlineAt: Date | null;
  publishedAt: Date | null;
  sourceRefs: unknown;
  urls: unknown;
};

export type MatchCard = {
  id: string;
  score: number;
  breakdown: unknown;
  state: string;
  dismissReason: string | null;
  notice: NoticeSummary;
};

export type MatchPage = { data: MatchCard[]; nextCursor: string | null };
export type MatchTransition = { id: string; state: string; dismissReason: string | null };
export type PursueResult = { tenderId: string; stage: string };

@ApiTags("matches")
@Controller({ path: "matches", version: "1" })
export class MatchesController {
  // Explicit token: esbuild cannot emit `design:paramtypes`, so implicit
  // constructor injection would resolve to undefined under vitest.
  constructor(@Inject(TenantService) private readonly tenant: TenantService) {}

  @Get()
  @ApiOperation({ summary: "List matches for the current org, newest and highest-scoring first" })
  async list(
    @Req() request: Request,
    @Query("state") state?: string,
    @Query("minScore") minScore?: string,
    @Query("cursor") cursor?: string,
    @Query("limit") limit?: string,
  ): Promise<MatchPage> {
    const principal = await this.tenant.resolve(request);
    const take = Math.min(Number(limit) || 25, MAX_PAGE_SIZE);
    const min = minScore ? Number(minScore) : undefined;
    if (min !== undefined && (Number.isNaN(min) || min < 0 || min > 100)) {
      throw new BadRequestException("minScore must be between 0 and 100");
    }

    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const rows = await tx.match.findMany({
        where: {
          ...(state ? { state: state as MatchState } : {}),
          ...(min !== undefined ? { score: { gte: min } } : {}),
        },
        // Cursor pagination everywhere (§19). Score-then-id keeps the order total and stable.
        orderBy: [{ score: "desc" }, { id: "asc" }],
        ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
        take: take + 1,
        include: {
          notice: {
            select: {
              id: true,
              title: true,
              buyerName: true,
              country: true,
              cpv: true,
              nuts: true,
              amountEst: true,
              currency: true,
              deadlineAt: true,
              publishedAt: true,
              sourceRefs: true,
              urls: true,
            },
          },
        },
      });

      const page = rows.slice(0, take);
      return {
        data: page.map((row) => ({
          id: row.id,
          score: row.score,
          // The factor breakdown travels with every card: §8.4 requires the UI to be able
          // to show why a score is what it is (P4).
          breakdown: row.breakdown,
          state: row.state,
          dismissReason: row.dismissReason,
          notice: {
            ...row.notice,
            amountEst: row.notice.amountEst ? Number(row.notice.amountEst) : null,
          },
        })),
        nextCursor: rows.length > take ? page[page.length - 1]?.id ?? null : null,
      };
    });
  }

  @Post(":id/shortlist")
  async shortlist(@Req() request: Request, @Param("id") matchId: string): Promise<MatchTransition> {
    return this.transition(request, matchId, "shortlisted");
  }

  @Post(":id/dismiss")
  @ApiOperation({ summary: "Dismiss a match with a reason (feeds weight tuning, §8.3)" })
  async dismiss(
    @Req() request: Request,
    @Param("id") matchId: string,
    @Body() body: { reason?: string },
  ): Promise<MatchTransition> {
    const reason = body?.reason as DismissReason | undefined;
    if (!reason || !DISMISS_REASONS.includes(reason)) {
      throw new BadRequestException(`reason must be one of: ${DISMISS_REASONS.join(", ")}`);
    }
    return this.transition(request, matchId, "dismissed", reason);
  }

  @Post(":id/pursue")
  @ApiOperation({ summary: "Pursue a match: creates the tender workspace (§12)" })
  async pursue(@Req() request: Request, @Param("id") matchId: string): Promise<PursueResult> {
    const principal = await this.tenant.resolve(request);
    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const match = await tx.match.findUnique({ where: { id: matchId }, include: { notice: true } });
      if (!match) {
        // RLS already scoped the read, so "not found" here also covers "belongs to another
        // org" - and says nothing about whether that id exists elsewhere.
        throw new NotFoundException("match not found");
      }

      // Idempotent: pursuing twice must not create a second workspace for one notice.
      const existing = await tx.tender.findFirst({ where: { noticeId: match.noticeId } });
      const tender =
        existing ??
        (await tx.tender.create({
          data: {
            id: id("tnd"),
            orgId: principal.orgId,
            noticeId: match.noticeId,
            origin: "match",
            // P2: a pursued tender starts in analysis, never straight in production.
            stage: "analysis",
            title: match.notice.title,
            deadlineAt: match.notice.deadlineAt,
          },
        }));

      await tx.match.update({ where: { id: matchId }, data: { state: "pursued" } });
      await tx.event.create({
        data: {
          id: id("evt"),
          orgId: principal.orgId,
          actor: principal.userId,
          kind: "match.pursued",
          entity: tender.id,
          payload: { matchId, noticeId: match.noticeId },
        },
      });

      return { tenderId: tender.id, stage: tender.stage };
    });
  }

  private async transition(
    request: Request,
    matchId: string,
    state: MatchState,
    dismissReason?: DismissReason,
  ): Promise<MatchTransition> {
    const principal = await this.tenant.resolve(request);
    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      // updateMany rather than update: it returns a count instead of throwing, so an id
      // belonging to another org is indistinguishable from one that does not exist.
      const result = await tx.match.updateMany({
        where: { id: matchId },
        data: { state, dismissReason: dismissReason ?? null },
      });
      if (result.count === 0) {
        throw new NotFoundException("match not found");
      }
      await tx.event.create({
        data: {
          id: id("evt"),
          orgId: principal.orgId,
          actor: principal.userId,
          kind: `match.${state}`,
          entity: matchId,
          payload: dismissReason ? { reason: dismissReason } : {},
        },
      });
      return { id: matchId, state, dismissReason: dismissReason ?? null };
    });
  }
}

/** Global market data: readable without a tenant context (§18.1). */
@ApiTags("notices")
@Controller({ path: "notices", version: "1" })
export class NoticesController {
  @Get(":id")
  async get(@Param("id") noticeId: string): Promise<Record<string, unknown>> {
    const notice = await getPrisma().notice.findUnique({
      where: { id: noticeId },
      include: { documents: true },
    });
    if (!notice) throw new NotFoundException("notice not found");
    return {
      ...notice,
      amountEst: notice.amountEst ? Number(notice.amountEst) : null,
    };
  }
}
