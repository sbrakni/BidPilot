/**
 * Auth.js configuration (SPEC §17.1: "email magic link + Google/Microsoft OAuth").
 *
 * Three decisions worth stating, because each has a plausible alternative:
 *
 *   * **Database sessions, not JWTs.** A JWT stays valid until it expires, so signing out or
 *     removing someone from an org would not take effect until then - and the API would have to
 *     share a signing secret with the web app to verify one. A session row can be deleted, and
 *     the API verifies by looking it up (see `apps/api/src/common/tenant.ts`).
 *   * **The adapter connects as `bidpilot_auth`**, a role with privileges on the four
 *     authentication tables and nothing else, so this file cannot reach tenant data even by
 *     mistake (ADR-0015).
 *   * **OAuth providers are conditional on configuration.** A provider button that leads to a
 *     misconfiguration error is worse than no button, and the spec's own reasoning for OAuth is
 *     that SMEs live in Microsoft accounts - which is a deployment fact, not a build-time one.
 */

import NextAuth, { type NextAuthConfig } from "next-auth";
import { PrismaAdapter } from "@auth/prisma-adapter";
import Nodemailer from "next-auth/providers/nodemailer";
import Google from "next-auth/providers/google";
import MicrosoftEntraId from "next-auth/providers/microsoft-entra-id";

import { getAuthPrisma, id } from "@bidpilot/db";

/** Magic links expire fast: this is a credential sitting in an inbox (SPEC §24.4). */
const MAGIC_LINK_MAX_AGE_SECONDS = 15 * 60;

/** Sessions last a working fortnight, refreshed on use, so a bid team is not re-authenticating mid-consultation. */
const SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60;

export type OAuthProviderId = "google" | "microsoft-entra-id";

/**
 * OAuth providers, each gated on its own credentials being present.
 *
 * Auth.js reads `AUTH_<PROVIDER>_ID`/`_SECRET` from the environment itself, so the check here is
 * only about whether to offer the provider at all. A button that leads to a configuration error
 * is worse than an absent button.
 */
const OAUTH_PROVIDERS: ReadonlyArray<{
  id: OAuthProviderId;
  provider: NextAuthConfig["providers"][number];
  requires: readonly string[];
}> = [
  { id: "google", provider: Google, requires: ["AUTH_GOOGLE_ID", "AUTH_GOOGLE_SECRET"] },
  {
    id: "microsoft-entra-id",
    provider: MicrosoftEntraId,
    requires: [
      "AUTH_MICROSOFT_ENTRA_ID_ID",
      "AUTH_MICROSOFT_ENTRA_ID_SECRET",
      "AUTH_MICROSOFT_ENTRA_ID_ISSUER",
    ],
  },
];

function configured(): typeof OAUTH_PROVIDERS {
  return OAUTH_PROVIDERS.filter((entry) => entry.requires.every((key) => process.env[key]));
}

/** Which OAuth buttons the sign-in page should offer. Read on the server; safe to render. */
export function configuredOAuthProviders(): OAuthProviderId[] {
  return configured().map((entry) => entry.id);
}

/**
 * Built per request rather than once at module load.
 *
 * `PrismaAdapter` needs a connected client, so evaluating this eagerly would make
 * `DATABASE_AUTH_URL` a *build-time* requirement - `next build` collects page data by importing
 * the route, and the build would fail on a machine that has no database, which is every CI
 * machine that only compiles. Auth.js v5 accepts a factory for exactly this reason.
 */
export function authConfig(): NextAuthConfig {
  return {
    adapter: {
      ...PrismaAdapter(getAuthPrisma()),
      // Every id in this schema is a prefixed ULID (§18); the adapter would otherwise use cuid,
      // leaving `users` with two id shapes depending on how the row was created.
      createUser: async (data) => {
        const prisma = getAuthPrisma();
        return prisma.user.create({ data: { ...data, id: id("usr") } });
      },
    },
    session: { strategy: "database", maxAge: SESSION_MAX_AGE_SECONDS },
    pages: {
      signIn: "/sign-in",
      verifyRequest: "/sign-in/check-email",
      error: "/sign-in",
    },
    providers: [
      Nodemailer({
        server: process.env.EMAIL_SERVER ?? "smtp://localhost:1025",
        from: process.env.EMAIL_FROM ?? "BidPilot <no-reply@bidpilot.example>",
        maxAge: MAGIC_LINK_MAX_AGE_SECONDS,
      }),
      ...configured().map((entry) => entry.provider),
    ],
    callbacks: {
      // `user` is the database row, so the id here is the one every other table joins on.
      // Without this the session carries an email and no id, and nothing can be scoped to it.
      session: ({ session, user }) => ({
        ...session,
        user: { ...session.user, id: user.id },
      }),
    },
    trustHost: true,
  };
}

export const { handlers, signIn, signOut, auth } = NextAuth(authConfig);
