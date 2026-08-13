/**
 * SIRET/SIREN bootstrap via the French *Recherche d'entreprises* API (SPEC §7.1).
 *
 * This is the P5 promise in one call: "SIRET in → live matching tenders out", with §7.4 asking
 * for identity pre-filled in under five seconds. So it runs inline rather than through a job.
 * That is a considered exception to §17.2's "api never calls external portals inline": the rule
 * exists for portal scraping, which is slow and fragile, whereas this is a fast official API on
 * the critical path of the product's first impression. The timeout below is what keeps the
 * exception safe.
 *
 * Field shapes verified against the live API (fixtures/entreprises/*.json), and two of them are
 * traps:
 *   * `activite_principale` is NAF **with a dot** ("70.10Z"), while our naf_cpv_map is keyed
 *     without one.
 *   * `tranche_effectif_salarie` is an INSEE **band code** ("01" = 1-2 employees), not a
 *     headcount. Storing it as a number would invent precision the source never gave (P1), so
 *     the band is preserved and only a range is derived.
 *   * `commune` is an INSEE code ("75113"); the human name is `libelle_commune` ("PARIS").
 */

import { proxyAwareFetch } from "../common/http.js";

const API_BASE = "https://recherche-entreprises.api.gouv.fr/search";

/** §7.4 asks for identity in ≤5s; this bounds the whole request well inside that. */
const TIMEOUT_MS = 4_000;

/**
 * INSEE "tranche d'effectif salarié" nomenclature. The band is authoritative; the range is
 * derived. A null upper bound means "and above".
 */
export const EFFECTIF_BANDS: Record<string, { min: number; max: number | null }> = {
  NN: { min: 0, max: 0 },
  "00": { min: 0, max: 0 },
  "01": { min: 1, max: 2 },
  "02": { min: 3, max: 5 },
  "03": { min: 6, max: 9 },
  "11": { min: 10, max: 19 },
  "12": { min: 20, max: 49 },
  "21": { min: 50, max: 99 },
  "22": { min: 100, max: 199 },
  "31": { min: 200, max: 249 },
  "32": { min: 250, max: 499 },
  "41": { min: 500, max: 999 },
  "42": { min: 1_000, max: 1_999 },
  "51": { min: 2_000, max: 4_999 },
  "52": { min: 5_000, max: 9_999 },
  "53": { min: 10_000, max: null },
};

/**
 * French département → NUTS-2, mirroring `dept_to_nuts` in the ingestion service.
 *
 * Duplicated deliberately and narrowly: the alternative is an HTTP hop to Python on the
 * onboarding critical path, and this is static reference data that changes with NUTS revisions
 * (every few years), not with our code. Both copies are tested against the same expectations.
 */
const DEPT_TO_NUTS: Record<string, string> = {
  ...Object.fromEntries(["75", "77", "78", "91", "92", "93", "94", "95"].map((d) => [d, "FR10"])),
  ...Object.fromEntries(["18", "28", "36", "37", "41", "45"].map((d) => [d, "FRB0"])),
  ...Object.fromEntries(["21", "58", "71", "89"].map((d) => [d, "FRC1"])),
  ...Object.fromEntries(["25", "39", "70", "90"].map((d) => [d, "FRC2"])),
  ...Object.fromEntries(["14", "50", "61"].map((d) => [d, "FRD1"])),
  ...Object.fromEntries(["27", "76"].map((d) => [d, "FRD2"])),
  ...Object.fromEntries(["59", "62"].map((d) => [d, "FRE1"])),
  ...Object.fromEntries(["02", "60", "80"].map((d) => [d, "FRE2"])),
  ...Object.fromEntries(["67", "68"].map((d) => [d, "FRF1"])),
  ...Object.fromEntries(["08", "10", "51", "52"].map((d) => [d, "FRF2"])),
  ...Object.fromEntries(["54", "55", "57", "88"].map((d) => [d, "FRF3"])),
  ...Object.fromEntries(["44", "49", "53", "72", "85"].map((d) => [d, "FRG0"])),
  ...Object.fromEntries(["22", "29", "35", "56"].map((d) => [d, "FRH0"])),
  ...Object.fromEntries(["24", "33", "40", "47", "64"].map((d) => [d, "FRI1"])),
  ...Object.fromEntries(["19", "23", "87"].map((d) => [d, "FRI2"])),
  ...Object.fromEntries(["16", "17", "79", "86"].map((d) => [d, "FRI3"])),
  ...Object.fromEntries(["11", "30", "34", "48", "66"].map((d) => [d, "FRJ1"])),
  ...Object.fromEntries(["09", "12", "31", "32", "46", "65", "81", "82"].map((d) => [d, "FRJ2"])),
  ...Object.fromEntries(["03", "15", "43", "63"].map((d) => [d, "FRK1"])),
  ...Object.fromEntries(["01", "07", "26", "38", "42", "69", "73", "74"].map((d) => [d, "FRK2"])),
  ...Object.fromEntries(["04", "05", "06", "13", "83", "84"].map((d) => [d, "FRL0"])),
  ...Object.fromEntries(["2A", "2B", "20"].map((d) => [d, "FRM0"])),
  "971": "FRY1",
  "972": "FRY2",
  "973": "FRY3",
  "974": "FRY4",
  "976": "FRY5",
};

/** INSEE commune codes are five characters, digits or a Corsican 2A/2B prefix. */
function isCommuneCode(value: string | null | undefined): boolean {
  return typeof value === "string" && /^(\d{5}|2[AB]\d{3})$/.test(value.trim());
}

export function deptToNuts(dept: string | null | undefined): string | null {
  if (!dept) return null;
  const code = /^\d$/.test(dept.trim()) ? dept.trim().padStart(2, "0") : dept.trim().toUpperCase();
  return DEPT_TO_NUTS[code] ?? null;
}

/** Strip the dot INSEE uses so the code matches our `naf_cpv_map` keys. */
export function normalizeNaf(naf: string | null | undefined): string | null {
  if (!naf) return null;
  const cleaned = naf.replace(/\./g, "").toUpperCase().trim();
  return cleaned || null;
}

export type CompanyIdentity = {
  siren: string;
  siret: string | null;
  legalName: string;
  naf: string | null;
  nafLabel: string | null;
  /** The INSEE band code, kept verbatim - it is what the source actually stated. */
  effectifBand: string | null;
  headcountRange: { min: number; max: number | null } | null;
  createdAt: string | null;
  address: {
    street: string | null;
    postcode: string | null;
    city: string | null;
    department: string | null;
    nuts: string | null;
  };
  executives: Array<{ name: string; role: string | null }>;
  /** Present only when the API states it; never derived. */
  legalCategory: string | null;
};

type ApiEtablissement = {
  siret?: string;
  adresse?: string;
  code_postal?: string;
  /** INSEE commune *code* ("75113"), not a name - `libelle_commune` carries the name. */
  commune?: string;
  libelle_commune?: string;
  departement?: string;
  libelle_voie?: string;
  numero_voie?: string;
  type_voie?: string;
};

type ApiResult = {
  siren?: string;
  nom_complet?: string;
  nom_raison_sociale?: string;
  activite_principale?: string;
  section_activite_principale?: string;
  tranche_effectif_salarie?: string;
  date_creation?: string;
  categorie_entreprise?: string;
  etat_administratif?: string;
  siege?: ApiEtablissement;
  matching_etablissements?: ApiEtablissement[];
  dirigeants?: Array<{ nom?: string; prenoms?: string; qualite?: string; denomination?: string }>;
};

export class EntrepriseLookupError extends Error {
  constructor(
    readonly reason: "not_found" | "unavailable" | "invalid_input",
    message: string,
  ) {
    super(message);
    this.name = "EntrepriseLookupError";
  }
}

/** A SIREN is 9 digits, a SIRET 14 (SIREN + establishment). */
export function parseIdentifier(input: string): { siren: string; siret: string | null } {
  const digits = (input ?? "").replace(/\D/g, "");
  if (digits.length === 9) return { siren: digits, siret: null };
  if (digits.length === 14) return { siren: digits.slice(0, 9), siret: digits };
  throw new EntrepriseLookupError(
    "invalid_input",
    "a SIREN (9 digits) or SIRET (14 digits) is required",
  );
}

export function mapIdentity(result: ApiResult, requestedSiret: string | null): CompanyIdentity {
  const siren = (result.siren ?? "").replace(/\D/g, "");
  const establishment =
    (requestedSiret
      ? result.matching_etablissements?.find((e) => e.siret === requestedSiret)
      : undefined) ?? result.siege;
  const band = result.tranche_effectif_salarie ?? null;
  const department = establishment?.departement ?? null;

  return {
    siren,
    siret: requestedSiret ?? establishment?.siret ?? null,
    legalName: result.nom_complet || result.nom_raison_sociale || siren,
    naf: normalizeNaf(result.activite_principale),
    nafLabel: result.section_activite_principale ?? null,
    effectifBand: band,
    // Absent means absent (P1): an unknown band yields no range rather than a guessed zero.
    headcountRange: band && EFFECTIF_BANDS[band] ? EFFECTIF_BANDS[band] : null,
    createdAt: result.date_creation ?? null,
    address: {
      street: establishment?.adresse ?? null,
      postcode: establishment?.code_postal ?? null,
      // `commune` is an INSEE code; only `libelle_commune` is a name. Establishment entries
      // sometimes omit the label, and showing "75113" as a city is worse than showing nothing -
      // the postcode beside it already locates the company (P1: absent means absent).
      city: establishment?.libelle_commune ?? (isCommuneCode(establishment?.commune) ? null : establishment?.commune ?? null),
      department,
      nuts: deptToNuts(department),
    },
    executives: (result.dirigeants ?? [])
      .map((person) => ({
        name: person.denomination || [person.prenoms, person.nom].filter(Boolean).join(" ").trim(),
        role: person.qualite ?? null,
      }))
      .filter((person) => person.name.length > 0)
      .slice(0, 10),
    legalCategory: result.categorie_entreprise ?? null,
  };
}

/**
 * Look up a company. `fetchImpl` is injectable so tests run against the captured fixture rather
 * than the live API - a test suite that depends on data.gouv.fr being up is a flaky suite.
 */
export async function lookupCompany(
  identifier: string,
  fetchImpl: typeof fetch = proxyAwareFetch,
): Promise<CompanyIdentity> {
  const { siren, siret } = parseIdentifier(identifier);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let payload: { results?: ApiResult[] };
  try {
    const response = await fetchImpl(`${API_BASE}?q=${siren}&per_page=1`, {
      signal: controller.signal,
      headers: { Accept: "application/json", "User-Agent": "BidPilot/1.0" },
    });
    if (!response.ok) {
      throw new EntrepriseLookupError("unavailable", `lookup failed with ${response.status}`);
    }
    payload = (await response.json()) as { results?: ApiResult[] };
  } catch (error) {
    if (error instanceof EntrepriseLookupError) throw error;
    // A slow or down registry must degrade to manual entry, not block onboarding entirely.
    throw new EntrepriseLookupError("unavailable", (error as Error).message);
  } finally {
    clearTimeout(timer);
  }

  const result = payload.results?.find((r) => (r.siren ?? "").replace(/\D/g, "") === siren);
  if (!result) {
    throw new EntrepriseLookupError("not_found", `no company found for ${siren}`);
  }
  return mapIdentity(result, siret);
}
