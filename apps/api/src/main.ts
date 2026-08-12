import "reflect-metadata";

import { Logger, ValidationPipe, VersioningType } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import { DocumentBuilder, SwaggerModule } from "@nestjs/swagger";

import { AppModule } from "./app.module.js";

/**
 * API entrypoint (SPEC §17.2, §19).
 *
 * The same OpenAPI document that types the web client becomes the public API's
 * documentation on the Scale plan (§19), so it is generated rather than written.
 */
async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule, { cors: true });

  // URI versioning: /v1/... . The public contract needs a version from day one.
  app.enableVersioning({ type: VersioningType.URI, defaultVersion: "1" });
  app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }));

  const document = SwaggerModule.createDocument(
    app,
    new DocumentBuilder()
      .setTitle("BidPilot API")
      .setDescription("Decision-first bid platform. Internal first; the same spec becomes the public API.")
      .setVersion("1.0")
      .build(),
  );
  SwaggerModule.setup("docs", app, document);

  const port = Number(process.env.API_PORT ?? 3001);
  await app.listen(port);
  new Logger("bootstrap").log(`api listening on :${port} (OpenAPI at /docs)`);
}

void bootstrap();
