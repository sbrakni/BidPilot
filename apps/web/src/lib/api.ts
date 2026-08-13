/**
 * Typed client for the BidPilot API (SPEC §17.2: "web ↔ api only").
 *
 * Every piece of tenant data in this app arrives through here. That is not ceremony: it means
 * isolation has exactly one enforcement point - the API's authorization plus Postgres RLS behind
 * it - and the same contract serves the public API on the Scale plan (§19).
 *
 * The web app does hold one database connection, for Auth.js alone. Its role is granted the four
 * authentication tables and nothing else, so "tenant data comes only from the API" is enforced by
 * Postgres rather than by the absence of a connection string (ADR-0015).
 */

import { cookies } from "next/headers";

const API_BASE = process.env.API_PUBLIC_URL ?? "http://localhost:3001";

/**
 * Auth.js session cookie names. The `__Secure-` form is used whenever the site is served over
 * HTTPS, so both have to be looked for rather than deciding from NODE_ENV - a preview
 * deployment is production-built and may be either.
 */
const SESSION_COOKIES = ["__Secure-authjs.session-token", "authjs.session-token"] as const;

/**
 * The credential this app presents to the API.
 *
 * With database sessions the cookie value *is* the session token, so forwarding it lets the API
 * verify identity by looking the session up - no signing secret shared between the two services,
 * and revocation is immediate because it is a row.
 */
async function sessionToken(): Promise<string | undefined> {
  const store = await cookies();
  for (const name of SESSION_COOKIES) {
    const value = store.get(name)?.value;
    if (value) return value;
  }
  return undefined;
}

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

export type TenderCard = {
  id: string;
  title: string;
  stage: "analysis" | "decision" | "response" | "submitted" | "closed";
  origin: "match" | "manual" | "email";
  deadlineAt: string | null;
  sourceUrl: string | null;
  notice: {
    id: string;
    buyerName: string | null;
    cpv: string[];
    amountEst: number | null;
    currency: string | null;
    urls: unknown;
  } | null;
};

export type TenderList = { data: TenderCard[]; countsByStage: Record<string, number> };

export type EvidenceCard = {
  id: string;
  kind: "admin" | "certification" | "reference" | "people" | "content" | "template";
  title: string;
  issuer: string | null;
  issuedAt: string | null;
  expiresAt: string | null;
  status: "valid" | "expiring" | "expired";
  tags: string[];
  daysUntilExpiry: number | null;
};

export type VaultHealth = {
  total: number;
  valid: number;
  expiring: number;
  expired: number;
  byKind: Record<string, number>;
};

export type LibraryPage = { data: EvidenceCard[]; health: VaultHealth };

export type ReferenceCard = {
  id: string;
  title: string;
  client: string;
  clientType: string;
  cpv: string[];
  amount: number | null;
  periodStart: string | null;
  periodEnd: string | null;
  location: string | null;
  matchedCpv: string[];
};

export type OrgSummary = {
  id: string;
  name: string;
  siren: string | null;
  locale: string;
  tz: string;
  plan: string;
  role: string;
  /** `sources+{org}@{domain}` for the email connector (§6.5); null when not configured. */
  inboundEmail: string | null;
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
  const token = await sessionToken();

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
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

  tenders: () => request<TenderList>("/v1/tenders"),

  library: (params: { kind?: string; status?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.kind) query.set("kind", params.kind);
    if (params.status) query.set("status", params.status);
    const suffix = query.toString();
    return request<LibraryPage>(`/v1/library/evidence${suffix ? `?${suffix}` : ""}`);
  },

  references: (cpv?: string[]) =>
    request<{ data: ReferenceCard[] }>(
      `/v1/library/references${cpv?.length ? `?cpv=${cpv.join(",")}` : ""}`,
    ),

  matches: (params: { state?: string; minScore?: number; limit?: number; cursor?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.state && params.state !== "all") query.set("state", params.state);
    if (params.minScore) query.set("minScore", String(params.minScore));
    query.set("limit", String(params.limit ?? 25));
    if (params.cursor) query.set("cursor", params.cursor);
    return request<MatchPage>(`/v1/matches?${query.toString()}`);
  },

  bootstrapProfile: (identifier: string) =>
    request<{ identity: Record<string, unknown>; suggested: { cpvFamilies: string[] } }>(
      "/v1/profile/bootstrap",
      { method: "POST", body: JSON.stringify({ siret: identifier }) },
    ),

  saveProfile: (input: {
    cpvFamilies: string[];
    zones: { nuts: string[]; national: boolean; max_distance_km: number | null };
    revenues: Array<{ year: number; amount: number }>;
  }) => request("/v1/profile", { method: "PUT", body: JSON.stringify(input) }),

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
