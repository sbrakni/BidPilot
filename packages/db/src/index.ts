/**
 * Database access for BidPilot (SPEC §17.6).
 *
 * The only supported way to touch org-scoped data is {@link withOrgContext}. It opens a
 * transaction, sets `app.org_id` for that transaction alone, and runs the callback.
 * Postgres RLS policies then filter every statement inside it.
 *
 * Two details that matter more than they look:
 *
 *  - `set_config(..., is_local = true)` scopes the setting to the transaction. Prisma
 *    pools connections, so a session-level `SET` would leak one tenant's context into
 *    the next request that happened to reuse the connection. That is the bug class this
 *    whole module exists to make impossible.
 *  - With no context set, policies match nothing. The default is "see nothing", not
 *    "see everything" - so a code path that forgets to establish context fails loudly
 *    and empty instead of leaking.
 */

import { PrismaClient } from "@prisma/client";
import { ulid } from "ulid";

export type { Prisma } from "@prisma/client";
export * from "@prisma/client";

/** ULID ids, per SPEC §18 conventions. Prefixed so an id is self-describing in logs. */
export function id(prefix: string): string {
  return `${prefix}_${ulid()}`;
}

let client: PrismaClient | undefined;

/**
 * The shared client. Connects with `DATABASE_APP_URL` when present - the non-owner role
 * that RLS actually applies to - falling back to `DATABASE_URL` for migrations and tools.
 */
export function getPrisma(): PrismaClient {
  if (!client) {
    const url = process.env.DATABASE_APP_URL ?? process.env.DATABASE_URL;
    if (!url) {
      throw new Error("DATABASE_APP_URL or DATABASE_URL must be set");
    }
    client = new PrismaClient({ datasources: { db: { url } } });
  }
  return client;
}

export type OrgScopedClient = Omit<
  PrismaClient,
  "$connect" | "$disconnect" | "$on" | "$transaction" | "$use" | "$extends"
>;

/** Who is acting, and on behalf of which org. */
export type TenantContext = { orgId: string; userId?: string };

/**
 * Run `fn` with the tenant context set.
 *
 * The API calls this once per request, *after* authorization has confirmed the user
 * belongs to the org. Authorization decides which org; RLS enforces that decision.
 *
 * Passing `userId` additionally scopes the policies that are about a person rather than a
 * tenant - editing your own profile, for instance (see the `users` policies).
 */
export async function withOrgContext<T>(
  context: string | TenantContext,
  fn: (tx: OrgScopedClient) => Promise<T>,
  prisma: PrismaClient = getPrisma(),
): Promise<T> {
  const { orgId, userId } = typeof context === "string" ? { orgId: context, userId: undefined } : context;
  if (!orgId) {
    throw new Error("withOrgContext requires a non-empty orgId");
  }
  return prisma.$transaction(async (tx) => {
    await tx.$executeRaw`SELECT set_config('app.org_id', ${orgId}, true)`;
    // Empty string reads back as NULL through app_current_user_id(), which matches nothing.
    await tx.$executeRaw`SELECT set_config('app.user_id', ${userId ?? ""}, true)`;
    return fn(tx as unknown as OrgScopedClient);
  });
}

/**
 * Run `fn` with no tenant context, for the global market data in §18.1.
 *
 * Reading notices, sources and awards needs no org: they are the commons. The
 * application role holds SELECT and nothing else on those tables, so this cannot be
 * used to write them even by mistake.
 */
export async function withGlobalContext<T>(
  fn: (tx: OrgScopedClient) => Promise<T>,
  prisma: PrismaClient = getPrisma(),
): Promise<T> {
  return prisma.$transaction(async (tx) => fn(tx as unknown as OrgScopedClient));
}

export async function disconnect(): Promise<void> {
  if (client) {
    await client.$disconnect();
    client = undefined;
  }
}
