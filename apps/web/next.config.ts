import createNextIntlPlugin from "next-intl/plugin";
import type { NextConfig } from "next";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The web app talks only to the API (SPEC §17.2), so the workspace packages it shares
  // with the API are transpiled rather than pre-built.
  transpilePackages: ["@bidpilot/shared"],
};

export default withNextIntl(nextConfig);
