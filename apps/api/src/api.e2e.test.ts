/**
 * API-level tests, run against the seeded demo database.
 *
 * §15.5 requires that "a user of org A can never read org B rows **through any API path**".
 * The RLS suite in packages/db proves the database enforces it; this suite proves the HTTP
 * surface does not offer a way around it - a route that leaks is a leak, however sound the
 * policies underneath are.
 *
 * Requires `pnpm db:seed` to have run (CI does this before `pnpm test`).
 */

import "reflect-metadata";

import { INestApplication, VersioningType } from "@nestjs/common";
import { Test } from "@nestjs/testing";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { PrismaClient, withUserContext } from "@bidpilot/db";

import { AppModule } from "./app.module.js";

// Seeded personas (SPEC §3.1): Léa runs the ESN, Sofia the facilities company. The constants
// hold session *tokens* now, not user ids - the API accepts no credential it cannot verify.
const LEA = "sess_test_lea";
const SOFIA = "sess_test_sofia";
const EXPIRED = "sess_test_expired";
const ORPHANED = "sess_test_unknown";

const HOUR = 60 * 60 * 1000;

let app: INestApplication;
let server: ReturnType<INestApplication["getHttpServer"]>;
let owner: PrismaClient;

/**
 * Sessions are written with the owner connection, not the API's.
 *
 * Not incidental: the application role is granted SELECT on `sessions` and nothing else, so it
 * *cannot* create one (ADR-0015). A fixture that could would be proving something the running
 * system does not allow.
 */
beforeAll(async () => {
  owner = new PrismaClient({ datasources: { db: { url: process.env.DATABASE_URL } } });
  await owner.session.deleteMany({
    where: { sessionToken: { in: [LEA, SOFIA, EXPIRED, ORPHANED] } },
  });
  await owner.session.createMany({
    data: [
      { sessionToken: LEA, userId: "user_lea", expires: new Date(Date.now() + HOUR) },
      { sessionToken: SOFIA, userId: "user_sofia", expires: new Date(Date.now() + HOUR) },
      { sessionToken: EXPIRED, userId: "user_lea", expires: new Date(Date.now() - HOUR) },
    ],
  });

  const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
  app = moduleRef.createNestApplication();
  app.enableVersioning({ type: VersioningType.URI, defaultVersion: "1" });
  await app.init();
  server = app.getHttpServer();
}, 60_000);

afterAll(async () => {
  await owner?.session.deleteMany({
    where: { sessionToken: { in: [LEA, SOFIA, EXPIRED, ORPHANED] } },
  });
  await owner?.$disconnect();
  await app?.close();
});

describe("health", () => {
  it("reports liveness without authentication", async () => {
    const response = await request(server).get("/health").expect(200);
    expect(response.body).toMatchObject({ status: "ok" });
  });

  it("reports database readiness separately from liveness", async () => {
    const response = await request(server).get("/health/ready").expect(200);
    expect(response.body.checks.database).toBe("ok");
  });
});

describe("authentication", () => {
  it("refuses an unauthenticated request", async () => {
    await request(server).get("/v1/org").expect(401);
  });

  it("accepts a live session", async () => {
    await request(server).get("/v1/org").set("authorization", `Bearer ${LEA}`).expect(200);
  });

  it("refuses a token that matches no session", async () => {
    await request(server).get("/v1/org").set("authorization", `Bearer ${ORPHANED}`).expect(401);
  });

  it("refuses an expired session", async () => {
    // The row exists and names a real user; only `expires` is in the past. This is the case a
    // hand-written expiry comparison gets wrong, so it is asserted rather than assumed.
    await request(server).get("/v1/org").set("authorization", `Bearer ${EXPIRED}`).expect(401);
  });

  it("refuses a user id presented as though it were a session token", async () => {
    // The credential the API used to accept. It must now be worth nothing.
    await request(server).get("/v1/org").set("authorization", "Bearer user_lea").expect(401);
  });

  it("ignores a credential in a scheme it does not implement", async () => {
    await request(server).get("/v1/org").set("authorization", `Basic ${LEA}`).expect(401);
  });

  it("accepts the bearer scheme case-insensitively, per RFC 6750", async () => {
    await request(server).get("/v1/org").set("authorization", `bEaReR ${LEA}`).expect(200);
  });

  it("refuses a session whose user belongs to no organisation", async () => {
    const token = "sess_test_orgless";
    const userId = "usr_test_orgless";
    // Created inside its own user context: `users` is FORCE-RLS'd, and Prisma's create issues
    // INSERT ... RETURNING, so the row has to be visible to the SELECT policy to come back.
    await withUserContext(
      userId,
      (tx) => tx.user.create({ data: { id: userId, email: "orgless@example.test", name: "No Org" } }),
      owner,
    );
    await owner.session.create({
      data: { sessionToken: token, userId, expires: new Date(Date.now() + HOUR) },
    });
    try {
      // 403, not 401: they are who they say they are, they just have nothing to act on.
      await request(server).get("/v1/org").set("authorization", `Bearer ${token}`).expect(403);
    } finally {
      await owner.session.delete({ where: { sessionToken: token } });
      await withUserContext(userId, (tx) => tx.user.delete({ where: { id: userId } }), owner);
    }
  });
});

describe("org scoping", () => {
  it("returns the acting user's own org", async () => {
    const response = await request(server).get("/v1/org").set("authorization", `Bearer ${LEA}`).expect(200);
    expect(response.body.id).toBe("org_demo_esn");
    expect(response.body.role).toBe("owner");
    expect(response.body.counters.newMatches).toBeGreaterThan(0);
  });

  it("cannot be pointed at another org by header", async () => {
    // Even naming a real org id must fail: membership is checked, not trusted.
    await request(server)
      .get("/v1/org")
      .set("authorization", `Bearer ${LEA}`)
      .set("x-bidpilot-org", "org_demo_maintenance")
      .expect(403);
  });
});

describe("match inbox", () => {
  it("returns scored matches whose factors sum to the displayed score (SPEC §8.4)", async () => {
    const response = await request(server)
      .get("/v1/matches?limit=10")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);

    expect(response.body.data.length).toBeGreaterThan(0);
    for (const match of response.body.data) {
      const total = match.breakdown.factors.reduce(
        (sum: number, factor: { points: number }) => sum + factor.points,
        0,
      );
      expect(Math.round(total)).toBe(match.breakdown.score);
      expect(match.score).toBe(match.breakdown.score);
    }
  });

  it("rejects an out-of-range minScore", async () => {
    await request(server).get("/v1/matches?minScore=900").set("authorization", `Bearer ${LEA}`).expect(400);
  });

  it("paginates by cursor", async () => {
    const first = await request(server).get("/v1/matches?limit=2").set("authorization", `Bearer ${LEA}`).expect(200);
    expect(first.body.nextCursor).toBeTruthy();

    const second = await request(server)
      .get(`/v1/matches?limit=2&cursor=${first.body.nextCursor}`)
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);

    const firstIds = first.body.data.map((m: { id: string }) => m.id);
    const secondIds = second.body.data.map((m: { id: string }) => m.id);
    expect(firstIds.some((id: string) => secondIds.includes(id))).toBe(false);
  });

  it("never returns another org's matches", async () => {
    const lea = await request(server).get("/v1/matches?limit=100").set("authorization", `Bearer ${LEA}`).expect(200);
    const sofia = await request(server)
      .get("/v1/matches?limit=100")
      .set("authorization", `Bearer ${SOFIA}`)
      .expect(200);

    const leaIds = new Set(lea.body.data.map((m: { id: string }) => m.id));
    const sofiaIds = new Set(sofia.body.data.map((m: { id: string }) => m.id));
    expect(leaIds.size).toBeGreaterThan(0);
    expect(sofiaIds.size).toBeGreaterThan(0);
    expect([...leaIds].some((id) => sofiaIds.has(id))).toBe(false);
  });

  it("refuses to act on another org's match, and does not reveal that it exists", async () => {
    const lea = await request(server).get("/v1/matches?limit=1").set("authorization", `Bearer ${LEA}`).expect(200);
    const foreignId = lea.body.data[0].id;

    // 404, not 403: a 403 would confirm the id is real to someone with no right to know.
    await request(server).post(`/v1/matches/${foreignId}/shortlist`).set("authorization", `Bearer ${SOFIA}`).expect(404);
    await request(server)
      .post(`/v1/matches/${foreignId}/dismiss`)
      .set("authorization", `Bearer ${SOFIA}`)
      .send({ reason: "no_time" })
      .expect(404);
    await request(server).post(`/v1/matches/${foreignId}/pursue`).set("authorization", `Bearer ${SOFIA}`).expect(404);
  });

  it("requires a known dismissal reason (SPEC §8.3)", async () => {
    const lea = await request(server).get("/v1/matches?limit=1").set("authorization", `Bearer ${LEA}`).expect(200);
    const matchId = lea.body.data[0].id;

    await request(server)
      .post(`/v1/matches/${matchId}/dismiss`)
      .set("authorization", `Bearer ${LEA}`)
      .send({ reason: "just because" })
      .expect(400);

    const ok = await request(server)
      .post(`/v1/matches/${matchId}/dismiss`)
      .set("authorization", `Bearer ${LEA}`)
      .send({ reason: "too_big" })
      .expect(201);
    expect(ok.body).toMatchObject({ state: "dismissed", dismissReason: "too_big" });

    // Restore the seeded state so the suite stays re-runnable.
    await request(server).post(`/v1/matches/${matchId}/shortlist`).set("authorization", `Bearer ${LEA}`).expect(201);
  });

  it("creates at most one tender per notice when pursued twice (P2: starts in analysis)", async () => {
    const lea = await request(server).get("/v1/matches?limit=5").set("authorization", `Bearer ${LEA}`).expect(200);
    const matchId = lea.body.data[lea.body.data.length - 1].id;

    const first = await request(server)
      .post(`/v1/matches/${matchId}/pursue`)
      .set("authorization", `Bearer ${LEA}`)
      .expect(201);
    const second = await request(server)
      .post(`/v1/matches/${matchId}/pursue`)
      .set("authorization", `Bearer ${LEA}`)
      .expect(201);

    expect(second.body.tenderId).toBe(first.body.tenderId);
    expect(first.body.stage).toBe("analysis");
  });
});

describe("global market data (SPEC §18.1)", () => {
  it("serves a notice by id", async () => {
    const lea = await request(server).get("/v1/matches?limit=1").set("authorization", `Bearer ${LEA}`).expect(200);
    const noticeId = lea.body.data[0].notice.id;
    const response = await request(server).get(`/v1/notices/${noticeId}`).expect(200);
    expect(response.body.id).toBe(noticeId);
    expect(response.body.title).toBeTruthy();
  });

  it("404s an unknown notice", async () => {
    await request(server).get("/v1/notices/ntc_does_not_exist").expect(404);
  });
});

describe("public coverage status (SPEC §6.9)", () => {
  it("is served without authentication and reports per-source freshness", async () => {
    // Unauthenticated on purpose: coverage transparency only builds trust if anyone can check
    // it. It carries operational facts only - no notice content, no tenant data.
    const response = await request(server).get("/v1/status/coverage").expect(200);

    expect(response.body.summary.total).toBeGreaterThan(0);
    expect(response.body.sources.length).toBe(response.body.summary.total);

    for (const source of response.body.sources) {
      expect(source).toHaveProperty("code");
      expect(source).toHaveProperty("health");
      expect(source).toHaveProperty("hoursSinceSuccess");
      // Every source must carry a recorded legal basis or be disabled (SPEC §24.7).
      expect(source.legalBasis !== null || source.enabled === false).toBe(true);
    }
  });

  it("declares the gaps we know about rather than leaving them to be discovered", async () => {
    const response = await request(server).get("/v1/status/coverage").expect(200);
    expect(response.body.knownGaps.join(" ")).toMatch(/JAL/);
  });
});

describe("inbound email webhook (SPEC §6.5)", () => {
  const SECRET = "test-inbound-secret";
  const payload = {
    to: "sources+org_demo_esn@in.bidpilot.example",
    from: "alertes@marches.example",
    subject: "Nouvelle consultation",
    text: "https://marches.example-hospital.fr/consultation/8801",
    messageId: "<webhook-test@example>",
  };

  beforeAll(() => {
    process.env.INBOUND_EMAIL_WEBHOOK_SECRET = SECRET;
  });

  afterAll(async () => {
    delete process.env.INBOUND_EMAIL_WEBHOOK_SECRET;
    await owner?.$executeRawUnsafe(
      `DELETE FROM jobs WHERE idempotency_key LIKE 'email.inbound:%'`,
    );
  });

  it("refuses a request with no secret", async () => {
    await request(server).post("/v1/inbound/email").send(payload).expect(401);
  });

  it("refuses a wrong secret", async () => {
    await request(server)
      .post("/v1/inbound/email")
      .set("x-bidpilot-inbound-secret", "not-the-secret")
      .send(payload)
      .expect(401);
  });

  it("refuses a session token in place of the shared secret", async () => {
    // The endpoint is machine-to-machine; a user credential must not open it.
    await request(server)
      .post("/v1/inbound/email")
      .set("x-bidpilot-inbound-secret", LEA)
      .send(payload)
      .expect(401);
  });

  it("accepts a signed delivery and queues it for the worker", async () => {
    const response = await request(server)
      .post("/v1/inbound/email")
      .set("x-bidpilot-inbound-secret", SECRET)
      .send(payload)
      .expect(202);

    expect(response.body.accepted).toBe(true);
    expect(response.body.jobId).toBeTruthy();

    const job = await owner.job.findFirst({
      where: { idempotencyKey: `email.inbound:${payload.messageId}:1` },
    });
    expect(job?.kind).toBe("email.inbound");
    // The API stores the message and parses nothing: that belongs to the ingestion worker.
    expect((job?.payload as { to?: string }).to).toBe(payload.to);
  });

  it("does not queue the same message twice when the provider retries", async () => {
    await request(server)
      .post("/v1/inbound/email")
      .set("x-bidpilot-inbound-secret", SECRET)
      .send(payload)
      .expect(202);

    const count = await owner.job.count({
      where: { idempotencyKey: `email.inbound:${payload.messageId}:1` },
    });
    expect(count).toBe(1);
  });

  it("accepts but does not queue a message it cannot attribute to an org", async () => {
    // 202 rather than 4xx: the provider cannot fix a missing recipient by re-sending, and an
    // error status would have it redeliver the same message indefinitely.
    const response = await request(server)
      .post("/v1/inbound/email")
      .set("x-bidpilot-inbound-secret", SECRET)
      .send({ ...payload, to: undefined, messageId: "<no-recipient@example>" })
      .expect(202);
    expect(response.body.jobId).toBeNull();
  });
});

describe("tender list (SPEC §20.1)", () => {
  it("returns the org's tenders, soonest deadline first", async () => {
    const response = await request(server)
      .get("/v1/tenders")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);

    expect(Array.isArray(response.body.data)).toBe(true);
    // P3: a published deadline always outranks an absent one, whatever else is true of the row.
    const deadlines: Array<string | null> = response.body.data.map(
      (tender: { deadlineAt: string | null }) => tender.deadlineAt,
    );
    const withDeadline = deadlines.filter(Boolean) as string[];
    const sorted = [...withDeadline].sort();
    expect(withDeadline).toEqual(sorted);
    expect(deadlines.slice(withDeadline.length).every((value) => value === null)).toBe(true);
  });

  it("counts by stage so the screen can show where work sits", async () => {
    const response = await request(server)
      .get("/v1/tenders")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);
    const total = Object.values(response.body.countsByStage as Record<string, number>).reduce(
      (sum, n) => sum + n,
      0,
    );
    expect(total).toBe(response.body.data.length);
  });

  it("never shows another org's tenders", async () => {
    const [lea, sofia] = await Promise.all([
      request(server).get("/v1/tenders").set("authorization", `Bearer ${LEA}`).expect(200),
      request(server).get("/v1/tenders").set("authorization", `Bearer ${SOFIA}`).expect(200),
    ]);
    const leaIds = new Set(lea.body.data.map((t: { id: string }) => t.id));
    const overlap = sofia.body.data.filter((t: { id: string }) => leaIds.has(t.id));
    expect(overlap).toEqual([]);
  });

  it("requires authentication", async () => {
    await request(server).get("/v1/tenders").expect(401);
  });
});

describe("evidence vault (SPEC §7.2, §7.4)", () => {
  it("returns the org's evidence with health counters over the whole vault", async () => {
    const response = await request(server)
      .get("/v1/library/evidence")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);

    expect(response.body.data.length).toBeGreaterThan(0);
    const { health } = response.body;
    // The §7.4 criterion: the counters must add up, or the widget is decoration.
    expect(health.valid + health.expiring + health.expired).toBe(health.total);
    expect(health.total).toBe(response.body.data.length);
  });

  it("keeps the health counters over the whole vault when the list is filtered", async () => {
    // A widget that changed when you clicked a filter would be reporting the filter.
    const [all, filtered] = await Promise.all([
      request(server).get("/v1/library/evidence").set("authorization", `Bearer ${LEA}`).expect(200),
      request(server)
        .get("/v1/library/evidence?kind=certification")
        .set("authorization", `Bearer ${LEA}`)
        .expect(200),
    ]);
    expect(filtered.body.health.total).toBe(all.body.health.total);
    expect(filtered.body.data.every((e: { kind: string }) => e.kind === "certification")).toBe(true);
  });

  it("orders by what expires soonest, because the list is a worklist", async () => {
    const response = await request(server)
      .get("/v1/library/evidence")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);
    const withExpiry: string[] = response.body.data
      .map((e: { expiresAt: string | null }) => e.expiresAt)
      .filter(Boolean);
    expect(withExpiry).toEqual([...withExpiry].sort());
  });

  it("rejects an unknown kind rather than silently returning everything", async () => {
    await request(server)
      .get("/v1/library/evidence?kind=nonsense")
      .set("authorization", `Bearer ${LEA}`)
      .expect(400);
  });

  it("never shows another org's evidence", async () => {
    const [lea, sofia] = await Promise.all([
      request(server).get("/v1/library/evidence").set("authorization", `Bearer ${LEA}`).expect(200),
      request(server).get("/v1/library/evidence").set("authorization", `Bearer ${SOFIA}`).expect(200),
    ]);
    const leaIds = new Set(lea.body.data.map((e: { id: string }) => e.id));
    expect(sofia.body.data.filter((e: { id: string }) => leaIds.has(e.id))).toEqual([]);
  });

  it("requires authentication", async () => {
    await request(server).get("/v1/library/evidence").expect(401);
  });
});

describe("reference suggestions (SPEC §7.4)", () => {
  it("surfaces a 72* reference for a 72* tender, matching on the family", async () => {
    // The acceptance criterion verbatim: exact-code matching would suggest almost nothing,
    // because a reference's CPV rarely equals the notice's.
    const response = await request(server)
      .get("/v1/library/references?cpv=72")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);

    expect(response.body.data.length).toBeGreaterThan(0);
    for (const reference of response.body.data) {
      expect(reference.matchedCpv.length).toBeGreaterThan(0);
      // And it says *why* it was suggested, per P4.
      expect(reference.matchedCpv.every((code: string) => code.startsWith("72"))).toBe(true);
    }
  });

  it("returns nothing for a family the org has no reference in", async () => {
    const response = await request(server)
      .get("/v1/library/references?cpv=45")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);
    expect(response.body.data).toEqual([]);
  });

  it("returns the whole library when no family is asked for", async () => {
    const response = await request(server)
      .get("/v1/library/references")
      .set("authorization", `Bearer ${LEA}`)
      .expect(200);
    expect(response.body.data.length).toBeGreaterThan(0);
  });

  it("rejects a CPV that is not a code or a family prefix", async () => {
    await request(server)
      .get("/v1/library/references?cpv=abc")
      .set("authorization", `Bearer ${LEA}`)
      .expect(400);
  });
});
