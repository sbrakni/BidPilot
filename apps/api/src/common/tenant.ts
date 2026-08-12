/**
 * Tenant resolution (SPEC §15.1, §17.6).
 *
 * Authorization decides *which* org a request may act on; Postgres RLS enforces that
 * decision. This module is only the first half - it resolves and verifies membership, then
 * hands the org id to `withOrgContext`, which is where isolation actually happens.
 *
 * Phase 0 accepts an `x-bidpilot-user` header in development so the API is exercisable
 * before Auth.js sessions land in Phase 1. It is refused outright when NODE_ENV=production:
 * a dev shortcut that survives into production is an authentication bypass.
 */

import { ForbiddenException, Injectable, UnauthorizedException } from "@nestjs/common";
import type { Request } from "express";

import { getPrisma, withUserContext, type OrgRole } from "@bidpilot/db";

export type Principal = {
  userId: string;
  orgId: string;
  role: OrgRole;
};

const DEV_USER_HEADER = "x-bidpilot-user";
const ORG_HEADER = "x-bidpilot-org";

@Injectable()
export class TenantService {
  /**
   * Resolve the acting principal, verifying membership against `org_members`.
   *
   * Membership is read in *user* context, not org context: this is the check that
   * establishes the org, so reading it from inside the target org would be circular. The
   * `org_members` policy permits a user to read their own membership rows and nothing else.
   */
  async resolve(request: Request): Promise<Principal> {
    const userId = this.resolveUserId(request);
    const requestedOrgId = request.header(ORG_HEADER) ?? undefined;

    const memberships = await withUserContext(
      userId,
      (tx) => tx.orgMember.findMany({ orderBy: { createdAt: "asc" } }),
      getPrisma(),
    );

    if (memberships.length === 0) {
      throw new ForbiddenException("user belongs to no organisation");
    }

    // Multi-org membership is normal for consultants (§15.1): honour an explicit choice,
    // and otherwise default to the first org joined.
    const membership = requestedOrgId
      ? memberships.find((m) => m.orgId === requestedOrgId)
      : memberships[0];

    if (!membership) {
      // Deliberately the same error as "no membership": distinguishing them would confirm
      // that an org id exists to someone who is not a member of it.
      throw new ForbiddenException("user is not a member of the requested organisation");
    }

    return { userId, orgId: membership.orgId, role: membership.role };
  }

  private resolveUserId(request: Request): string {
    const devUser = request.header(DEV_USER_HEADER);
    if (devUser) {
      if (process.env.NODE_ENV === "production") {
        throw new UnauthorizedException(`${DEV_USER_HEADER} is not accepted in production`);
      }
      return devUser;
    }
    // Auth.js session wiring lands in Phase 1; until then there is no other credential,
    // and pretending otherwise would be worse than an explicit failure.
    throw new UnauthorizedException("authentication required");
  }
}

/** Roles allowed to override a submission gate or change billing (SPEC §11.1, §15.1). */
export function requireOwner(principal: Principal): void {
  if (principal.role !== "owner") {
    throw new ForbiddenException("this action requires the Owner role");
  }
}
