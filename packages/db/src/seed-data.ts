/**
 * Reference & seed data, kept as data rather than code (SPEC §4.2, Annex A, Annex D).
 *
 * Adding a source, a sector pack or a threshold revision is a data change here - no
 * engineering. That is the property that makes "one atexo adapter covers 30+ portals"
 * (§6.4) and "adding a sector pack costs zero engineering" true rather than aspirational.
 */

export type SourceSeed = {
  code: string;
  country: string;
  tier: "official_api" | "platform" | "long_tail" | "signals";
  kind: "api" | "ocds" | "rss" | "scrape" | "email" | "manual";
  adapter: string;
  config: Record<string, unknown>;
  schedule: string;
  legal: { basis: string; notes: string; reviewed_at: string | null };
  enabled: boolean;
};

/**
 * Annex A source catalog. `enabled` is false wherever the legal review is still open:
 * §24.7 requires a per-source review before Tier-2 scraping runs in production, and the
 * adapter itself refuses to start without a recorded basis.
 */
export const SOURCES: SourceSeed[] = [
  {
    code: "eu-ted",
    country: "EU",
    tier: "official_api",
    kind: "api",
    adapter: "ted",
    // Verified: the Search API needs no key, has no sort parameter, and validates
    // `fields` server-side (see services/ingestion/.../adapters/ted.py).
    config: { countries: ["FRA", "BEL", "LUX"], lookback_days: 2 },
    schedule: "*/30 * * * *",
    legal: {
      basis: "open-license",
      notes: "TED reuse permitted with source acknowledgment.",
      reviewed_at: "2026-08-12",
    },
    enabled: true,
  },
  {
    code: "fr-boamp",
    country: "FR",
    tier: "official_api",
    kind: "api",
    adapter: "boamp",
    config: { dataset: "boamp" },
    schedule: "*/30 * * * *",
    legal: {
      basis: "open-license",
      notes: "Opendatasoft dataset under licence etalab-2.0.",
      reviewed_at: "2026-08-12",
    },
    enabled: true,
  },
  {
    code: "fr-decp",
    country: "FR",
    tier: "signals",
    kind: "api",
    adapter: "decp",
    config: { dataset: "decp-consolidated" },
    schedule: "0 4 * * *",
    legal: { basis: "open-license", notes: "DECP open data (data.gouv.fr).", reviewed_at: "2026-08-12" },
    enabled: false,
  },
  {
    code: "fr-place",
    country: "FR",
    tier: "official_api",
    kind: "scrape",
    adapter: "place",
    config: { base_url: "https://www.marches-publics.gouv.fr" },
    schedule: "0 */2 * * *",
    legal: { basis: "tos-reviewed", notes: "State platform; review pending.", reviewed_at: null },
    enabled: false,
  },
  {
    code: "be-bosa",
    country: "BE",
    tier: "official_api",
    kind: "scrape",
    adapter: "bosa",
    config: { base_url: "https://www.publicprocurement.be" },
    schedule: "0 */3 * * *",
    legal: { basis: "tos-reviewed", notes: "All Belgian levels publish here since 2023.", reviewed_at: null },
    enabled: false,
  },
  {
    code: "lu-pmp",
    country: "LU",
    tier: "platform",
    kind: "scrape",
    adapter: "atexo",
    // An atexo instance: one adapter family, configured per portal (SPEC §6.4).
    config: { base_url: "https://pmp.b2g.etat.lu", family: "atexo" },
    schedule: "0 6,18 * * *",
    legal: { basis: "tos-reviewed", notes: "Low volume, twice daily.", reviewed_at: null },
    enabled: false,
  },
  {
    code: "fr-maximilien",
    country: "FR",
    tier: "platform",
    kind: "scrape",
    adapter: "atexo",
    config: { base_url: "https://www.maximilien.fr", family: "atexo", region: "FR10" },
    schedule: "0 */4 * * *",
    legal: { basis: "tos-reviewed", notes: "Île-de-France platform; review pending.", reviewed_at: null },
    enabled: false,
  },
  {
    code: "fr-megalis",
    country: "FR",
    tier: "platform",
    kind: "scrape",
    adapter: "atexo",
    config: { base_url: "https://marches.megalisbretagne.org", family: "atexo", region: "FRH0" },
    schedule: "0 */4 * * *",
    legal: { basis: "tos-reviewed", notes: "Bretagne platform; review pending.", reviewed_at: null },
    enabled: false,
  },
  {
    code: "email-inbox",
    country: "EU",
    tier: "long_tail",
    kind: "email",
    adapter: "email",
    // The universal fallback (§6.5): covers any portal that can send an alert email,
    // including authenticated ones, using the user's own access.
    config: { address_pattern: "sources+{org}@{domain}" },
    schedule: "* * * * *",
    legal: { basis: "open-license", notes: "User-forwarded content.", reviewed_at: "2026-08-12" },
    enabled: true,
  },
  {
    code: "manual-import",
    country: "EU",
    tier: "long_tail",
    kind: "manual",
    adapter: "manual",
    config: {},
    schedule: "",
    legal: { basis: "open-license", notes: "User-submitted URL or file.", reviewed_at: "2026-08-12" },
    enabled: true,
  },
];

/**
 * Annex D thresholds. Dated rows, never hardcoded (§16 i18n/regulatory drift): the 2024-25
 * values below are indicative and MUST be re-verified at each biennial EU revision.
 */
export const THRESHOLDS = [
  { code: "eu-works", country: "EU", label: "Seuil UE - travaux", amount: 5538000, validFrom: "2024-01-01" },
  { code: "eu-supplies-central", country: "EU", label: "Seuil UE - fournitures/services, État", amount: 143000, validFrom: "2024-01-01" },
  { code: "eu-supplies-subcentral", country: "EU", label: "Seuil UE - fournitures/services, collectivités", amount: 221000, validFrom: "2024-01-01" },
  { code: "eu-utilities", country: "EU", label: "Seuil UE - entités adjudicatrices", amount: 443000, validFrom: "2024-01-01" },
  { code: "fr-no-publicity", country: "FR", label: "FR - dispense de publicité", amount: 40000, validFrom: "2024-01-01" },
  { code: "fr-adapted-publicity", country: "FR", label: "FR - publicité adaptée (40k-90k)", amount: 90000, validFrom: "2024-01-01" },
];

/**
 * §10.4 time-to-obtain table. This is what turns "you are missing a document" into a
 * decision: a tax attestation is same-day, an ISO certification takes months and is
 * therefore an effective NO-GO.
 */
export const DOCUMENT_LEAD_TIMES = [
  { documentCode: "attestation-fiscale", label: "Attestation fiscale", country: "FR", timeToObtain: "same_day", typicalDays: 0, validityDays: 365 },
  { documentCode: "attestation-urssaf", label: "Attestation de vigilance URSSAF", country: "FR", timeToObtain: "days", typicalDays: 3, validityDays: 180 },
  { documentCode: "kbis", label: "Extrait KBIS", country: "FR", timeToObtain: "same_day", typicalDays: 0, validityDays: 90 },
  { documentCode: "assurance-rc-pro", label: "Attestation RC professionnelle", country: "FR", timeToObtain: "days", typicalDays: 5, validityDays: 365 },
  { documentCode: "qualiopi", label: "Certification Qualiopi", country: "FR", timeToObtain: "months", typicalDays: 120, validityDays: 1095 },
  { documentCode: "iso-9001", label: "Certification ISO 9001", country: "FR", timeToObtain: "months", typicalDays: 180, validityDays: 1095 },
  { documentCode: "iso-27001", label: "Certification ISO 27001", country: "FR", timeToObtain: "months", typicalDays: 240, validityDays: 1095 },
];

/** Sector packs (SPEC §4.2): curated CPV sets, pure data. */
export const SECTOR_PACKS = {
  it: {
    labelFr: "IT & numérique",
    cpvFamilies: ["72", "48", "302", "79511"],
    keywords: ["infogérance", "développement", "informatique", "logiciel", "numérique", "hébergement"],
  },
  training: {
    labelFr: "Formation",
    cpvFamilies: ["805", "79632", "80533"],
    keywords: ["formation", "qualiopi", "apprentissage", "certification professionnelle"],
  },
  maintenance: {
    labelFr: "Maintenance & facilities",
    cpvFamilies: ["50", "45259", "90910", "71314"],
    keywords: ["maintenance", "entretien", "nettoyage", "multitechnique", "exploitation"],
  },
} as const;

/** CPV labels for the families the sector packs use - enough to make the demo legible. */
export const CPV_LABELS: Array<{ code: string; lang: string; label: string }> = [
  { code: "72000000", lang: "fr", label: "Services de technologies de l'information" },
  { code: "72000000", lang: "en", label: "IT services" },
  { code: "48000000", lang: "fr", label: "Logiciels et systèmes d'information" },
  { code: "48000000", lang: "en", label: "Software package and information systems" },
  { code: "30200000", lang: "fr", label: "Matériel et fournitures informatiques" },
  { code: "30200000", lang: "en", label: "Computer equipment and supplies" },
  { code: "80500000", lang: "fr", label: "Services de formation" },
  { code: "80500000", lang: "en", label: "Training services" },
  { code: "50000000", lang: "fr", label: "Services de réparation et d'entretien" },
  { code: "50000000", lang: "en", label: "Repair and maintenance services" },
  { code: "90910000", lang: "fr", label: "Services de nettoyage" },
  { code: "90910000", lang: "en", label: "Cleaning services" },
];

export const NUTS_LABELS: Array<{ code: string; lang: string; label: string }> = [
  { code: "FR10", lang: "fr", label: "Île-de-France" },
  { code: "FRH0", lang: "fr", label: "Bretagne" },
  { code: "FRK2", lang: "fr", label: "Rhône-Alpes" },
  { code: "FRL0", lang: "fr", label: "Provence-Alpes-Côte d'Azur" },
  { code: "FRI2", lang: "fr", label: "Limousin" },
  { code: "BE10", lang: "fr", label: "Région de Bruxelles-Capitale" },
  { code: "LU00", lang: "fr", label: "Luxembourg" },
];

/** NAF -> CPV suggestions for SIRET bootstrap (SPEC §7.1). */
export const NAF_CPV_MAP = [
  { naf: "6202A", cpv: ["72", "48"], weight: 1 },
  { naf: "6201Z", cpv: ["72", "48"], weight: 1 },
  { naf: "6203Z", cpv: ["72"], weight: 1 },
  { naf: "8559A", cpv: ["805"], weight: 1 },
  { naf: "8559B", cpv: ["805", "79632"], weight: 1 },
  { naf: "8110Z", cpv: ["50", "90910", "79713"], weight: 1 },
  { naf: "4321A", cpv: ["45259", "50"], weight: 1 },
];
