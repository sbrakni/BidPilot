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

import { AppModule } from "./app.module.js";

// Seeded personas (SPEC §3.1): Léa runs the ESN, Sofia the facilities company.
const LEA = "user_lea";
const SOFIA = "user_sofia";

let app: INestApplication;
let server: ReturnType<INestApplication["getHttpServer"]>;

beforeAll(async () => {
  const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
  app = moduleRef.createNestApplication();
  app.enableVersioning({ type: VersioningType.URI, defaultVersion: "1" });
  await app.init();
  server = app.getHttpServer();
}, 60_000);

afterAll(async () => {
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

  it("refuses a user with no organisation", async () => {
    await request(server).get("/v1/org").set("x-bidpilot-user", "user_nobody").expect(403);
  });
});

describe("org scoping", () => {
  it("returns the acting user's own org", async () => {
    const response = await request(server).get("/v1/org").set("x-bidpilot-user", LEA).expect(200);
    expect(response.body.id).toBe("org_demo_esn");
    expect(response.body.role).toBe("owner");
    expect(response.body.counters.newMatches).toBeGreaterThan(0);
  });

  it("cannot be pointed at another org by header", async () => {
    // Even naming a real org id must fail: membership is checked, not trusted.
    await request(server)
      .get("/v1/org")
      .set("x-bidpilot-user", LEA)
      .set("x-bidpilot-org", "org_demo_maintenance")
      .expect(403);
  });
});

describe("match inbox", () => {
  it("returns scored matches whose factors sum to the displayed score (SPEC §8.4)", async () => {
    const response = await request(server)
      .get("/v1/matches?limit=10")
      .set("x-bidpilot-user", LEA)
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
    await request(server).get("/v1/matches?minScore=900").set("x-bidpilot-user", LEA).expect(400);
  });

  it("paginates by cursor", async () => {
    const first = await request(server).get("/v1/matches?limit=2").set("x-bidpilot-user", LEA).expect(200);
    expect(first.body.nextCursor).toBeTruthy();

    const second = await request(server)
      .get(`/v1/matches?limit=2&cursor=${first.body.nextCursor}`)
      .set("x-bidpilot-user", LEA)
      .expect(200);

    const firstIds = first.body.data.map((m: { id: string }) => m.id);
    const secondIds = second.body.data.map((m: { id: string }) => m.id);
    expect(firstIds.some((id: string) => secondIds.includes(id))).toBe(false);
  });

  it("never returns another org's matches", async () => {
    const lea = await request(server).get("/v1/matches?limit=100").set("x-bidpilot-user", LEA).expect(200);
    const sofia = await request(server)
      .get("/v1/matches?limit=100")
      .set("x-bidpilot-user", SOFIA)
      .expect(200);

    const leaIds = new Set(lea.body.data.map((m: { id: string }) => m.id));
    const sofiaIds = new Set(sofia.body.data.map((m: { id: string }) => m.id));
    expect(leaIds.size).toBeGreaterThan(0);
    expect(sofiaIds.size).toBeGreaterThan(0);
    expect([...leaIds].some((id) => sofiaIds.has(id))).toBe(false);
  });

  it("refuses to act on another org's match, and does not reveal that it exists", async () => {
    const lea = await request(server).get("/v1/matches?limit=1").set("x-bidpilot-user", LEA).expect(200);
    const foreignId = lea.body.data[0].id;

    // 404, not 403: a 403 would confirm the id is real to someone with no right to know.
    await request(server).post(`/v1/matches/${foreignId}/shortlist`).set("x-bidpilot-user", SOFIA).expect(404);
    await request(server)
      .post(`/v1/matches/${foreignId}/dismiss`)
      .set("x-bidpilot-user", SOFIA)
      .send({ reason: "no_time" })
      .expect(404);
    await request(server).post(`/v1/matches/${foreignId}/pursue`).set("x-bidpilot-user", SOFIA).expect(404);
  });

  it("requires a known dismissal reason (SPEC §8.3)", async () => {
    const lea = await request(server).get("/v1/matches?limit=1").set("x-bidpilot-user", LEA).expect(200);
    const matchId = lea.body.data[0].id;

    await request(server)
      .post(`/v1/matches/${matchId}/dismiss`)
      .set("x-bidpilot-user", LEA)
      .send({ reason: "just because" })
      .expect(400);

    const ok = await request(server)
      .post(`/v1/matches/${matchId}/dismiss`)
      .set("x-bidpilot-user", LEA)
      .send({ reason: "too_big" })
      .expect(201);
    expect(ok.body).toMatchObject({ state: "dismissed", dismissReason: "too_big" });

    // Restore the seeded state so the suite stays re-runnable.
    await request(server).post(`/v1/matches/${matchId}/shortlist`).set("x-bidpilot-user", LEA).expect(201);
  });

  it("creates at most one tender per notice when pursued twice (P2: starts in analysis)", async () => {
    const lea = await request(server).get("/v1/matches?limit=5").set("x-bidpilot-user", LEA).expect(200);
    const matchId = lea.body.data[lea.body.data.length - 1].id;

    const first = await request(server)
      .post(`/v1/matches/${matchId}/pursue`)
      .set("x-bidpilot-user", LEA)
      .expect(201);
    const second = await request(server)
      .post(`/v1/matches/${matchId}/pursue`)
      .set("x-bidpilot-user", LEA)
      .expect(201);

    expect(second.body.tenderId).toBe(first.body.tenderId);
    expect(first.body.stage).toBe("analysis");
  });
});

describe("global market data (SPEC §18.1)", () => {
  it("serves a notice by id", async () => {
    const lea = await request(server).get("/v1/matches?limit=1").set("x-bidpilot-user", LEA).expect(200);
    const noticeId = lea.body.data[0].notice.id;
    const response = await request(server).get(`/v1/notices/${noticeId}`).expect(200);
    expect(response.body.id).toBe(noticeId);
    expect(response.body.title).toBeTruthy();
  });

  it("404s an unknown notice", async () => {
    await request(server).get("/v1/notices/ntc_does_not_exist").expect(404);
  });
});
