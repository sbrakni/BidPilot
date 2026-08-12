import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // The RLS suite talks to a real Postgres; policies are stateful, so no parallelism.
    fileParallelism: false,
    hookTimeout: 30_000,
    testTimeout: 30_000,
  },
});
