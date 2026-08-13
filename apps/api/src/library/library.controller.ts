/**
 * The evidence vault (SPEC §7.2, §7.3) and its health widget (§7.4).
 *
 * Two of §7.4's acceptance criteria are answered here:
 *
 *   * *"Vault-health widget counts expired/expiring correctly"* — the counts come from the
 *     `status` column, which the freshness engine recomputes per org rather than trusting what
 *     was written when the document was uploaded (§7.2). A vault that reports itself healthy
 *     because nobody recomputed it is the failure that matters: eligibility reads this.
 *   * *"A reference created with CPV 72* surfaces in the suggested references of any 72*
 *     tender workspace"* — `GET /v1/library/references?cpv=…`, matching on CPV prefix, because
 *     that is how CPV families work and an exact-code match would suggest almost nothing.
 */

import { BadRequestException, Controller, Get, Inject, Query, Req } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request } from "express";

import { withOrgContext } from "@bidpilot/db";

import { TenantService } from "../common/tenant.js";

/** A CPV code is eight digits; a family is any prefix of one. */
const CPV_PREFIX = /^[0-9]{2,8}$/;

export type EvidenceCard = {
  id: string;
  kind: string;
  title: string;
  issuer: string | null;
  issuedAt: string | null;
  expiresAt: string | null;
  status: string;
  tags: string[];
  /** Days until expiry; negative once it has passed, null when the document has no expiry. */
  daysUntilExpiry: number | null;
};

export type VaultHealth = {
  total: number;
  valid: number;
  expiring: number;
  expired: number;
  byKind: Record<string, number>;
};

export type LibraryPage = { data: EvidenceCard[]; health: VaultHealth };

export type ReferenceCard = {
  id: string;
  title: string;
  client: string;
  clientType: string;
  cpv: string[];
  amount: number | null;
  periodStart: string | null;
  periodEnd: string | null;
  location: string | null;
  /** Which requested family this reference matched, so the UI can say why it is suggested (P4). */
  matchedCpv: string[];
};

@ApiTags("library")
@Controller({ path: "library", version: "1" })
export class LibraryController {
  constructor(@Inject(TenantService) private readonly tenant: TenantService) {}

  @Get("evidence")
  @ApiOperation({ summary: "The org's evidence vault, with its health counters" })
  async evidence(
    @Req() request: Request,
    @Query("kind") kind?: string,
    @Query("status") status?: string,
  ): Promise<LibraryPage> {
    const principal = await this.tenant.resolve(request);

    const kinds = ["admin", "certification", "reference", "people", "content", "template"];
    if (kind && !kinds.includes(kind)) {
      throw new BadRequestException(`kind must be one of ${kinds.join(", ")}`);
    }
    const statuses = ["valid", "expiring", "expired"];
    if (status && !statuses.includes(status)) {
      throw new BadRequestException(`status must be one of ${statuses.join(", ")}`);
    }

    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const [rows, counts] = await Promise.all([
        tx.evidence.findMany({
          where: {
            ...(kind ? { kind: kind as never } : {}),
            ...(status ? { status: status as never } : {}),
          },
          // Soonest expiry first, nulls last: the list is a worklist, and a document with no
          // expiry never needs attention.
          orderBy: [{ expiresAt: { sort: "asc", nulls: "last" } }, { createdAt: "desc" }],
          take: 200,
        }),
        // Counted over the whole vault, never the filtered page: a health widget that changed
        // when you clicked a filter would be reporting the filter, not the vault.
        tx.evidence.groupBy({ by: ["status", "kind"], _count: { _all: true } }),
      ]);

      const health: VaultHealth = { total: 0, valid: 0, expiring: 0, expired: 0, byKind: {} };
      for (const group of counts) {
        const n = group._count._all;
        health.total += n;
        health[group.status as "valid" | "expiring" | "expired"] += n;
        health.byKind[group.kind] = (health.byKind[group.kind] ?? 0) + n;
      }

      return {
        data: rows.map((row) => ({
          id: row.id,
          kind: row.kind,
          title: row.title,
          issuer: row.issuer,
          issuedAt: row.issuedAt?.toISOString().slice(0, 10) ?? null,
          expiresAt: row.expiresAt?.toISOString().slice(0, 10) ?? null,
          status: row.status,
          tags: row.tags,
          daysUntilExpiry: daysUntil(row.expiresAt),
        })),
        health,
      };
    });
  }

  @Get("references")
  @ApiOperation({ summary: "Reference projects, optionally those matching a CPV family (§7.4)" })
  async references(
    @Req() request: Request,
    @Query("cpv") cpv?: string,
  ): Promise<{ data: ReferenceCard[] }> {
    const principal = await this.tenant.resolve(request);

    const families = (cpv ?? "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    for (const family of families) {
      if (!CPV_PREFIX.test(family)) {
        throw new BadRequestException(`'${family}' is not a CPV code or family prefix`);
      }
    }

    return withOrgContext({ orgId: principal.orgId, userId: principal.userId }, async (tx) => {
      const rows = await tx.referenceProject.findMany({
        orderBy: [{ periodEnd: { sort: "desc", nulls: "last" } }],
        take: 200,
      });

      // Prefix matching in application code rather than SQL: `cpv` is a text[], and the
      // alternative is a LIKE over an unnested array, which reads worse and cannot use an index
      // either. A reference library is tens of rows, not millions.
      const matched = rows
        .map((row) => ({
          row,
          matchedCpv: families.length
            ? row.cpv.filter((code) => families.some((family) => code.startsWith(family)))
            : [],
        }))
        .filter(({ matchedCpv }) => families.length === 0 || matchedCpv.length > 0);

      return {
        data: matched.map(({ row, matchedCpv }) => ({
          id: row.id,
          title: row.title,
          client: row.client,
          clientType: row.clientType,
          cpv: row.cpv,
          amount: row.amount ? Number(row.amount) : null,
          periodStart: row.periodStart?.toISOString().slice(0, 10) ?? null,
          periodEnd: row.periodEnd?.toISOString().slice(0, 10) ?? null,
          location: row.location,
          matchedCpv,
        })),
      };
    });
  }
}

/**
 * Whole days from today to `date`, negative once past.
 *
 * Computed from calendar dates rather than instants: `expires_at` is a DATE, and an attestation
 * that expires "on the 14th" does so at the end of the 14th wherever the user is. Doing this in
 * UTC milliseconds would make a document look expired a day early for anyone west of Greenwich.
 */
function daysUntil(date: Date | null): number | null {
  if (!date) return null;
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const target = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  return Math.round((target - today) / 86_400_000);
}
