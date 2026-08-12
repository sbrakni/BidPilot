/**
 * Liveness and readiness (SPEC §16 Observability).
 *
 * `/health` is liveness: the process is up. `/health/ready` is readiness: dependencies
 * answer. Keeping them separate matters for deploys - a pod that cannot reach Postgres
 * should stop receiving traffic without being restarted in a loop.
 */

import { Controller, Get, VERSION_NEUTRAL } from "@nestjs/common";
import { ApiTags } from "@nestjs/swagger";

import { getPrisma } from "@bidpilot/db";

@ApiTags("health")
// Version-neutral: probes and dashboards should not have to track an API version.
@Controller({ path: "health", version: VERSION_NEUTRAL })
export class HealthController {
  @Get()
  live() {
    return { status: "ok", service: "api" };
  }

  @Get("ready")
  async ready() {
    const checks: Record<string, "ok" | "error"> = {};
    try {
      await getPrisma().$queryRaw`SELECT 1`;
      checks.database = "ok";
    } catch {
      checks.database = "error";
    }
    const ready = Object.values(checks).every((value) => value === "ok");
    return { status: ready ? "ok" : "degraded", checks };
  }
}
