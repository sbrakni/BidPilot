import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Talks to a real Postgres and mutates match state; no parallelism.
    fileParallelism: false,
    hookTimeout: 60_000,
    testTimeout: 60_000,
  },
  esbuild: {
    // NestJS decorators need the legacy transform plus metadata emission.
    target: "es2022",
    tsconfigRaw: {
      compilerOptions: { experimentalDecorators: true, emitDecoratorMetadata: true },
    },
  },
});
