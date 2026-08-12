/**
 * Domain vocabulary shared by the API and the web app (SPEC §5, §20.4).
 *
 * The rule from §5: English domain terms in code, French terms preserved as literals where
 * they are proper nouns. CPV, NUTS and DUME are never translated.
 */

/** Left navigation, in build order (SPEC §20.1). Labels come from i18n keys, not from here. */
export const NAV_SECTIONS = [
  { key: "today", href: "/today", i18nKey: "nav.today" },
  { key: "opportunities", href: "/opportunities", i18nKey: "nav.opportunities" },
  { key: "tenders", href: "/tenders", i18nKey: "nav.tenders" },
  { key: "library", href: "/library", i18nKey: "nav.library" },
  { key: "intel", href: "/intel", i18nKey: "nav.intel" },
  { key: "settings", href: "/settings", i18nKey: "nav.settings" },
] as const;

/** Tender lifecycle stages (SPEC §20.1 "Mes AO"). P2: analysis and decision precede response. */
export const TENDER_STAGES = ["analysis", "decision", "response", "submitted", "closed"] as const;

/** Dismissal reasons (SPEC §8.3). A closed list, because free text cannot tune weights. */
export const DISMISS_REASONS = [
  "too_big",
  "too_small",
  "wrong_activity",
  "no_time",
  "bad_buyer",
  "other",
] as const;

/** Deadline alert offsets in working days (SPEC §12.4). */
export const DEADLINE_ALERT_DAYS = [14, 7, 3, 1] as const;

/** Instant push instead of the daily digest at or above this score (SPEC §8.3). */
export const INSTANT_ALERT_THRESHOLD = 80;

/**
 * Urgency band for a deadline chip (SPEC §20.2: colour by urgency; red is reserved
 * exclusively for eliminatory conditions and deadline danger, §20.3).
 */
export function deadlineUrgency(
  deadline: Date | string | null | undefined,
  now: Date = new Date(),
): "none" | "comfortable" | "soon" | "urgent" | "critical" | "passed" {
  if (!deadline) return "none";
  const at = deadline instanceof Date ? deadline : new Date(deadline);
  if (Number.isNaN(at.getTime())) return "none";
  const days = (at.getTime() - now.getTime()) / 86_400_000;
  if (days < 0) return "passed";
  if (days <= 1) return "critical";
  if (days <= 3) return "urgent";
  if (days <= 7) return "soon";
  return "comfortable";
}

/**
 * Format an amount the way French practitioners write it (SPEC §20.4: `1 250 000 € HT`).
 *
 * `vat` is only appended when the source actually stated it - asserting "HT" on an unknown
 * basis would misrepresent a price by 20%.
 */
export function formatAmount(
  amount: number | null | undefined,
  currency = "EUR",
  vat?: "HT" | "TTC" | null,
  locale = "fr-FR",
): string | null {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return null;
  const formatted = new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
  return vat ? `${formatted} ${vat}` : formatted;
}
