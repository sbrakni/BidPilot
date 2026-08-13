"use server";

/**
 * Server actions for inbox triage.
 *
 * They exist so the browser never holds an API credential and never talks to the API
 * directly: the request is made server-side with the session, and `revalidatePath` refreshes
 * the list. Errors are returned rather than thrown so a failed action shows next to the
 * button instead of replacing the page with an error boundary.
 */

import { revalidatePath } from "next/cache";

import { api, ApiError } from "@/lib/api";
import { signIn, signOut } from "@/auth";

type Result = { ok: boolean; error?: string };

/**
 * Send a magic link (SPEC §17.1).
 *
 * Auth.js redirects to the "check your email" page on success and back to sign-in with an
 * `error` query parameter otherwise - so this deliberately does not report whether the address
 * belongs to an existing account. That distinction is exactly what makes a sign-in form an
 * account-enumeration oracle.
 */
export async function startEmailSignIn(formData: FormData): Promise<void> {
  const email = String(formData.get("email") ?? "").trim();
  if (!email) return;
  await signIn("nodemailer", { email, redirectTo: "/today" });
}

export async function startOAuthSignIn(formData: FormData): Promise<void> {
  const provider = String(formData.get("provider") ?? "");
  if (provider !== "google" && provider !== "microsoft-entra-id") return;
  await signIn(provider, { redirectTo: "/today" });
}

export async function endSession(): Promise<void> {
  await signOut({ redirectTo: "/sign-in" });
}

async function run(action: () => Promise<unknown>): Promise<Result> {
  try {
    await action();
    revalidatePath("/opportunities");
    revalidatePath("/today");
    return { ok: true };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, error: error.message };
    return { ok: false, error: "unreachable" };
  }
}

export async function shortlistMatch(matchId: string): Promise<Result> {
  return run(() => api.shortlist(matchId));
}

export async function dismissMatch(matchId: string, reason: string): Promise<Result> {
  return run(() => api.dismiss(matchId, reason));
}

export async function pursueMatch(matchId: string): Promise<Result> {
  const result = await run(() => api.pursue(matchId));
  revalidatePath("/tenders");
  return result;
}

/**
 * Bootstrap the profile from a SIREN/SIRET (SPEC §7.1).
 *
 * The API's failure reason is passed back rather than a generic message, because the right next
 * action differs: a typo means "check the number", an outage means "enter it manually".
 */
export async function bootstrapProfile(
  identifier: string,
): Promise<{ ok: boolean; identity?: unknown; reason?: "invalid" | "notFound" | "unavailable" }> {
  try {
    const result = await api.bootstrapProfile(identifier);
    return { ok: true, identity: result.identity };
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 400) {
        return { ok: false, reason: /9 digits|9 chiffres/.test(error.message) ? "invalid" : "notFound" };
      }
      return { ok: false, reason: "unavailable" };
    }
    return { ok: false, reason: "unavailable" };
  }
}

export async function saveProfile(input: {
  cpvFamilies: string[];
  zones: { nuts: string[]; national: boolean; max_distance_km: number | null };
  revenues: Array<{ year: number; amount: number }>;
}): Promise<Result> {
  const result = await run(() => api.saveProfile(input));
  // The inbox is what the user is about to look at, and saving a profile changes it.
  revalidatePath("/opportunities");
  return result;
}

