/**
 * Typed client for the BidPilot API (SPEC §17.2: "web ↔ api only").
 *
 * The web app never touches the database. That is not ceremony: it means tenant isolation
 * has exactly one enforcement point (the API's authorization plus Postgres RLS behind it),
 * and the same contract serves the public API on the Scale plan (§19).
 */

import { cookies } from "next/headers";

const API_BASE = process.env.API_PUBLIC_URL ?? "http://localhost:3001";

/** Set by the demo sign-in page until Auth.js lands. See `session.ts`. */
export const DEMO_USER_COOKIE = "bp_demo_user";

export type ScoreFactor = {
  key: string;
  label_fr: string;
  value: number;
  weight: number;
  points: number;
  detail: string;
};

export type ScoreBreakdown = {
  score: number;
  factors: ScoreFactor[];
  warnings: string[];
};

export type SourceRef = { source: string; external_id: string; url?: string | null };

export type NoticeSummary = {
  id: string;
  title: string;
  buyerName: string | null;
  country: string;
  cpv: string[];
  nuts: string[];
  amountEst: number | null;
  currency: string | null;
  deadlineAt: string | null;
  publishedAt: string | null;
  sourceRefs: SourceRef[];
  urls: { notice?: string | null; documents?: string | null } | null;
};

export type MatchCard = {
  id: string;
  score: number;
  breakdown: ScoreBreakdown;
  state: "new" | "shortlisted" | "dismissed" | "pursued";
  dismissReason: string | null;
  notice: NoticeSummary;
};

export type MatchPage = { data: MatchCard[]; nextCursor: string | null };

export type OrgSummary = {
  id: string;
  name: string;
  siren: string | null;
  locale: string;
  tz: string;
  plan: string;
  role: string;
  profile: { headcount: number | null; cpvFamilies: string[]; keywords: string[] } | null;
  counters: { newMatches: number; evidenceNeedingAttention: number };
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const store = await cookies();
  const demoUser = store.get(DEMO_USER_COOKIE)?.value;

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(demoUser ? { "x-bidpilot-user": demoUser } : {}),
      ...init.headers,
    },
    // Tender data changes as ingestion runs and deadlines tick down, so nothing here is
    // safe to cache: a stale countdown is the failure mode P3 calls the worst there is.
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { message?: string };
      detail = body.message ?? detail;
    } catch {
      // Non-JSON error body; the status line is all we have.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  org: () => request<OrgSummary>("/v1/org"),

  matches: (params: { state?: string; minScore?: number; limit?: number; cursor?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.state && params.state !== "all") query.set("state", params.state);
    if (params.minScore) query.set("minScore", String(params.minScore));
    query.set("limit", String(params.limit ?? 25));
    if (params.cursor) query.set("cursor", params.cursor);
    return request<MatchPage>(`/v1/matches?${query.toString()}`);
  },

  shortlist: (matchId: string) => request(`/v1/matches/${matchId}/shortlist`, { method: "POST" }),
  dismiss: (matchId: string, reason: string) =>
    request(`/v1/matches/${matchId}/dismiss`, { method: "POST", body: JSON.stringify({ reason }) }),
  pursue: (matchId: string) =>
    request<{ tenderId: string; stage: string }>(`/v1/matches/${matchId}/pursue`, { method: "POST" }),
};

/** True when the API is unreachable, so pages can render a useful state rather than crash. */
export function isUnreachable(error: unknown): boolean {
  return error instanceof TypeError || (error instanceof ApiError && error.status >= 500);
}
