/**
 * Display helpers (SPEC §20.4).
 *
 * Dates read `jeu. 12 mars 2027, 12:00 (heure de Paris)` and amounts `1 250 000 € HT`,
 * because those are the forms practitioners already read. Anything else adds a translation
 * step for the user.
 */

export type Urgency = "none" | "comfortable" | "soon" | "urgent" | "critical" | "passed";

/** Deadline urgency band. Red is reserved for `critical` and `passed` only (§20.3). */
export function deadlineUrgency(deadline: string | null | undefined, now = new Date()): Urgency {
  if (!deadline) return "none";
  const at = new Date(deadline);
  if (Number.isNaN(at.getTime())) return "none";
  const days = (at.getTime() - now.getTime()) / 86_400_000;
  if (days < 0) return "passed";
  if (days <= 1) return "critical";
  if (days <= 3) return "urgent";
  if (days <= 7) return "soon";
  return "comfortable";
}

export function daysUntil(deadline: string | null | undefined, now = new Date()): number | null {
  if (!deadline) return null;
  const at = new Date(deadline);
  if (Number.isNaN(at.getTime())) return null;
  return Math.ceil((at.getTime() - now.getTime()) / 86_400_000);
}

/**
 * Format a deadline in the org's timezone.
 *
 * The timezone is shown explicitly because a submission deadline is defined by the buyer's
 * clock (§5), and a user in another zone reading a bare time will submit late.
 */
export function formatDeadline(
  deadline: string | null | undefined,
  locale: string,
  timeZone = "Europe/Paris",
): string | null {
  if (!deadline) return null;
  const at = new Date(deadline);
  if (Number.isNaN(at.getTime())) return null;
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(at);
}

export function formatDate(date: string | null | undefined, locale: string, timeZone = "Europe/Paris"): string | null {
  if (!date) return null;
  const at = new Date(date);
  if (Number.isNaN(at.getTime())) return null;
  return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric", timeZone }).format(at);
}

/**
 * Format an amount. `vat` is appended only when the source stated it - asserting "HT" on an
 * unknown basis would misstate a price by 20% (P1: absent means absent).
 */
export function formatAmount(
  amount: number | null | undefined,
  locale: string,
  currency = "EUR",
  vat?: "HT" | "TTC" | null,
): string | null {
  if (amount === null || amount === undefined) return null;
  const formatted = new Intl.NumberFormat(locale, {
    style: "currency",
    currency: currency || "EUR",
    maximumFractionDigits: 0,
  }).format(amount);
  return vat ? `${formatted} ${vat}` : formatted;
}

/** Human label for a source code, e.g. `fr-boamp` → `BOAMP` (§6.7 source badges). */
export function sourceLabel(code: string): string {
  const labels: Record<string, string> = {
    "eu-ted": "TED",
    "fr-boamp": "BOAMP",
    "fr-place": "PLACE",
    "fr-decp": "DECP",
    "be-bosa": "e-Procurement BE",
    "lu-pmp": "PMP Luxembourg",
    "fr-maximilien": "Maximilien",
    "fr-megalis": "Mégalis Bretagne",
    "email-inbox": "Email",
    "manual-import": "Import manuel",
  };
  return labels[code] ?? code;
}
