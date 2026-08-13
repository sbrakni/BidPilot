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

type Result = { ok: boolean; error?: string };

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
