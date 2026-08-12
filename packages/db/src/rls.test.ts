/**
 * The cross-tenant test suite SPEC §15.5 requires:
 *
 *   "RLS proven by tests: user of org A can never read org B rows through any API path."
 *
 * These tests run against a real Postgres with the migrations applied, connecting as the
 * non-owner application role. Nothing here is mocked: mocking the database would test our
 * belief about the policies rather than the policies.
 */

import { PrismaClient } from "@prisma/client";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { withGlobalContext, withOrgContext, withPurgeContext } from "./index.js";

const ownerUrl = process.env.DATABASE_URL;
const appUrl = process.env.DATABASE_APP_URL;

// Fixed ids so a failed run leaves recognisable rows behind.
const ORG_A = "org_rls_a";
const ORG_B = "org_rls_b";

let owner: PrismaClient;
let app: PrismaClient;

/**
 * Remove an org's fixture rows.
 *
 * Cleanup runs *inside* the org's context on purpose: the migration sets FORCE ROW LEVEL
 * SECURITY, so a context-less `DELETE FROM orgs` matches nothing and silently succeeds -
 * which is precisely how the first version of this fixture leaked rows between runs.
 */
async function resetOrg(orgId: string): Promise<void> {
  // `withPurgeContext` because `decisions` is append-only and cascades from `tenders`:
  // without the erasure flag, deleting the tender would be blocked by the trigger.
  await withPurgeContext(
    orgId,
    async (tx) => {
      for (const table of ["decisions", "tasks", "tenders", "org_members", "orgs"]) {
        const column = table === "orgs" ? "id" : "org_id";
        await tx.$executeRawUnsafe(`DELETE FROM ${table} WHERE ${column} = $1`, orgId);
      }
    },
    owner,
  );
}

beforeAll(async () => {
  if (!ownerUrl || !appUrl) {
    throw new Error("DATABASE_URL and DATABASE_APP_URL must be set to run the RLS suite");
  }
  owner = new PrismaClient({ datasources: { db: { url: ownerUrl } } });
  app = new PrismaClient({ datasources: { db: { url: appUrl } } });

  await resetOrg(ORG_A);
  await resetOrg(ORG_B);

  for (const [orgId, title] of [
    [ORG_A, "Infogérance - Org A"],
    [ORG_B, "Maintenance - Org B"],
  ] as const) {
    await withOrgContext(
      orgId,
      async (tx) => {
        await tx.org.create({
          data: { id: orgId, name: orgId, country: "FR", locale: "fr", tz: "Europe/Paris" },
        });
        await tx.tender.create({
          data: { id: `tender_${orgId}`, orgId, title, stage: "analysis", origin: "match" },
        });
        await tx.task.create({
          data: { id: `task_${orgId}`, orgId, title: `Tâche ${orgId}`, status: "todo" },
        });
      },
      owner,
    );
  }
});

afterAll(async () => {
  if (owner) {
    await resetOrg(ORG_A);
    await resetOrg(ORG_B);
    await owner.$disconnect();
  }
  if (app) await app.$disconnect();
});

describe("tenant isolation", () => {
  it("shows an org only its own tenders", async () => {
    const rows = await withOrgContext(ORG_A, (tx) => tx.tender.findMany(), app);
    expect(rows.map((r) => r.orgId)).toEqual([ORG_A]);
  });

  it("hides another org's tender even when its id is known", async () => {
    // The strongest form of the check: ask for org B's row *by primary key* from org A.
    const row = await withOrgContext(
      ORG_A,
      (tx) => tx.tender.findUnique({ where: { id: `tender_${ORG_B}` } }),
      app,
    );
    expect(row).toBeNull();
  });

  it.each(["tender", "task", "org"] as const)("isolates %s rows in both directions", async (model) => {
    type Row = { id: string };
    const fromA: Row[] = await withOrgContext(ORG_A, (tx) => (tx[model] as any).findMany(), app);
    const fromB: Row[] = await withOrgContext(ORG_B, (tx) => (tx[model] as any).findMany(), app);
    const idsA = fromA.map((r) => r.id);
    const idsB = fromB.map((r) => r.id);
    expect(idsA.some((v) => idsB.includes(v))).toBe(false);
    expect(idsA.length).toBeGreaterThan(0);
    expect(idsB.length).toBeGreaterThan(0);
  });

  it("returns nothing when no tenant context is established", async () => {
    // Fail closed: a code path that forgets to set context must see an empty result, not
    // the whole table.
    const rows = await withGlobalContext((tx) => tx.tender.findMany(), app);
    expect(rows).toEqual([]);
  });

  it("cannot count another org's rows through an aggregate", async () => {
    const count = await withOrgContext(ORG_A, (tx) => tx.tender.count(), app);
    expect(count).toBe(1);
  });

  it("cannot reach another org's rows through a relation traversal", async () => {
    // Relations are a classic leak path: the join is filtered too, because RLS applies to
    // every table the query touches, not just the one in the FROM clause.
    const org = await withOrgContext(
      ORG_A,
      (tx) => tx.org.findFirst({ include: { tenders: true, tasks: true } }),
      app,
    );
    expect(org?.id).toBe(ORG_A);
    expect(org?.tenders.every((t) => t.orgId === ORG_A)).toBe(true);
    expect(org?.tasks.every((t) => t.orgId === ORG_A)).toBe(true);
  });

  it("cannot reach another org's rows through raw SQL", async () => {
    // The point of enforcing isolation in the database: even hand-written SQL is filtered.
    const rows = await withOrgContext(
      ORG_A,
      (tx) => tx.$queryRawUnsafe<Array<{ org_id: string }>>("SELECT org_id FROM tenders"),
      app,
    );
    expect(rows.map((r) => r.org_id)).toEqual([ORG_A]);
  });

  it("refuses to write a row belonging to another org", async () => {
    await expect(
      withOrgContext(
        ORG_A,
        (tx) =>
          tx.tender.create({
            data: { id: "tender_smuggled", orgId: ORG_B, title: "Smuggled", stage: "analysis", origin: "match" },
          }),
        app,
      ),
    ).rejects.toThrow(/row-level security/i);
  });

  it("refuses to update another org's row", async () => {
    const result = await withOrgContext(
      ORG_A,
      (tx) => tx.tender.updateMany({ where: { id: `tender_${ORG_B}` }, data: { title: "hijacked" } }),
      app,
    );
    expect(result.count).toBe(0);

    const untouched = await withOrgContext(
      ORG_B,
      (tx) => tx.tender.findUnique({ where: { id: `tender_${ORG_B}` } }),
      app,
    );
    expect(untouched?.title).toBe("Maintenance - Org B");
  });

  it("refuses to delete another org's row", async () => {
    const result = await withOrgContext(
      ORG_A,
      (tx) => tx.tender.deleteMany({ where: { id: `tender_${ORG_B}` } }),
      app,
    );
    expect(result.count).toBe(0);
  });

  it("rejects an empty org context outright", async () => {
    await expect(withOrgContext("", async () => null, app)).rejects.toThrow(/non-empty orgId/);
  });
});

describe("shared market data (SPEC §18.1)", () => {
  it("is readable without a tenant context", async () => {
    await expect(withGlobalContext((tx) => tx.notice.count(), app)).resolves.toBeGreaterThanOrEqual(0);
  });

  it("is not writable by a tenant", async () => {
    // A tenant able to write the commons could poison every other tenant's feed.
    await expect(
      withOrgContext(
        ORG_A,
        (tx) =>
          tx.notice.create({
            data: { id: "notice_evil", noticeType: "competition", country: "FR", title: "Injected" },
          }),
        app,
      ),
    ).rejects.toThrow(/permission denied/i);
  });
});

describe("append-only invariants (SPEC §10.6)", () => {
  it("allows inserting a decision but never updating or deleting one", async () => {
    const tenderId = `tender_${ORG_A}`;
    const decisionId = "decision_rls_test";
    await withOrgContext(
      ORG_A,
      async (tx) => {
        await tx.decision.create({
          data: {
            id: decisionId,
            tenderId,
            orgId: ORG_A,
            verdict: "goif",
            eligibilityPct: 78,
            decidedBy: "user_test",
          },
        });
      },
      owner,
    );

    // A superseding decision must create a new record, so the log stays auditable.
    await expect(
      withOrgContext(
        ORG_A,
        (tx) => tx.decision.update({ where: { id: decisionId }, data: { verdict: "go" } }),
        app,
      ),
    ).rejects.toThrow(/append-only/i);

    await expect(
      withOrgContext(ORG_A, (tx) => tx.decision.delete({ where: { id: decisionId } }), app),
    ).rejects.toThrow(/append-only/i);

    // Cleanup goes through the erasure path - the only route that may delete history.
    await withPurgeContext(
      ORG_A,
      (tx) => tx.$executeRawUnsafe(`DELETE FROM decisions WHERE id = $1`, decisionId),
      owner,
    );
  });

  it("permits erasure only through the purge path (SPEC §15.5)", async () => {
    // GDPR erasure must remain possible even though the log is append-only. The flag is
    // transaction-local, so it cannot leak into a subsequent request on the same connection.
    const decisionId = "decision_purge_test";
    await withOrgContext(
      ORG_A,
      (tx) =>
        tx.decision.create({
          data: { id: decisionId, tenderId: `tender_${ORG_A}`, orgId: ORG_A, verdict: "go", decidedBy: "user_test" },
        }),
      owner,
    );

    await expect(
      withOrgContext(ORG_A, (tx) => tx.decision.delete({ where: { id: decisionId } }), owner),
    ).rejects.toThrow(/append-only/i);

    await expect(
      withPurgeContext(ORG_A, (tx) => tx.decision.delete({ where: { id: decisionId } }), owner),
    ).resolves.toMatchObject({ id: decisionId });
  });
});
