import { Module } from "@nestjs/common";

import { TenantService } from "./common/tenant.js";
import { HealthController } from "./health/health.controller.js";
import { MatchesController, NoticesController } from "./matches/matches.controller.js";
import { OrgsController } from "./orgs/orgs.controller.js";
import { ProfileController } from "./profile/profile.controller.js";
import { SourcesController } from "./sources/sources.controller.js";

/**
 * Modules mirror the spec's feature areas F1-F10 (SPEC §17.1), so a section of the spec
 * maps to a place in the code without a translation step.
 */
@Module({
  controllers: [
    HealthController,
    OrgsController,
    MatchesController,
    NoticesController,
    ProfileController,
    SourcesController,
  ],
  providers: [TenantService],
})
export class AppModule {}
