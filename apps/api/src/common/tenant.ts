/**
 * Tenant resolution (SPEC §15.1, §17.6).
 *
 * Authorization decides *which* org a request may act on; Postgres RLS enforces that
 * decision. This module is only the first half - it resolves and verifies membership, then
 * hands the org id to `withOrgContext`, which is where isolation actually happens.
 *
 * Authentication is an Auth.js session token, presented as a bearer credential and verified
 * here by looking it up. Verifying rather than trusting is the point: the web app holds the
 * session cookie, but nothing about a forwarded value proves it came from a real sign-in.
 *
 * Lookup, not signature check, because sessions are rows (see `apps/web/src/auth.ts`). It costs
 * one indexed query per request and buys immediate revocation - deleting the row ends the
 * session now, where a JWT would stay valid until it expired. The API's role holds SELECT on
 * `sessions` and nothing more, so this path cannot mint one.
 */

import { ForbiddenException, Injectable, UnauthorizedException } from "@nestjs/common";
import type { Request } from "express";

import { getPrisma, withUserContext, type OrgRole } from "@bidpilot/db";

export type Principal = {
  userId: string;
  orgId: string;
  role: OrgRole;
};

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
    const userId = await this.resolveUserId(request);
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

  /**
   * The user behind a presented session token, or an explicit refusal.
   *
   * Expiry is filtered in the query rather than compared afterwards, so an expired session is
   * indistinguishable from one that never existed - both simply match no row. That also means a
   * forgotten expiry check cannot become an accepted-forever session.
   */
  private async resolveUserId(request: Request): Promise<string> {
    const token = bearerToken(request);
    if (!token) {
      throw new UnauthorizedException("authentication required");
    }

    // No tenant context: this runs before an org is known, and `sessions` is not org-scoped.
    const session = await getPrisma().session.findFirst({
      where: { sessionToken: token, expires: { gt: new Date() } },
      select: { userId: true },
    });

    if (!session) {
      // One message for absent, unknown and expired alike. Telling them apart would confirm
      // that a token exists to whoever is guessing at them.
      throw new UnauthorizedException("invalid or expired session");
    }
    return session.userId;
  }
}

/**
 * The bearer credential, if one was presented.
 *
 * Case-insensitive on the scheme because `Bearer`, `bearer` and `BEARER` are all valid per
 * RFC 6750, and a client that picks the wrong case should get "unauthenticated", not a
 * confusing "no credential".
 */
function bearerToken(request: Request): string | undefined {
  const header = request.header("authorization");
  if (!header) return undefined;
  const [scheme, ...rest] = header.split(" ");
  if (scheme?.toLowerCase() !== "bearer") return undefined;
  const token = rest.join(" ").trim();
  return token.length > 0 ? token : undefined;
}

/** Roles allowed to override a submission gate or change billing (SPEC §11.1, §15.1). */
export function requireOwner(principal: Principal): void {
  if (principal.role !== "owner") {
    throw new ForbiddenException("this action requires the Owner role");
  }
}
