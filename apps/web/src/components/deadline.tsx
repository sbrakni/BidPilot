/**
 * Deadline countdown (SPEC P3: "Any screen showing a tender shows its countdown").
 *
 * Red is reserved for `critical` and `passed` (§20.3). The buyer's timezone is named
 * explicitly rather than silently converted, because the deadline is defined by the buyer's
 * clock (§5) and a user in another zone reading a bare local time will submit late.
 */

import { deadlineUrgency, daysUntil, formatDeadline, type Urgency } from "@/lib/format";

const TONES: Record<Urgency, string> = {
  passed: "bg-danger-subtle text-danger",
  critical: "bg-danger-subtle text-danger",
  urgent: "bg-warning-subtle text-warning",
  soon: "bg-accent-subtle text-accent",
  comfortable: "bg-surface text-text-muted",
  none: "bg-surface text-text-subtle",
};

export function DeadlineChip({
  deadline,
  locale,
  timeZone = "Europe/Paris",
  labels,
}: {
  deadline: string | null;
  locale: string;
  timeZone?: string;
  labels: { none: string; passed: string; daysLeft: (days: number) => string };
}) {
  const urgency = deadlineUrgency(deadline);
  const days = daysUntil(deadline);
  const formatted = formatDeadline(deadline, locale, timeZone);

  const text =
    urgency === "none"
      ? labels.none
      : urgency === "passed"
        ? labels.passed
        : labels.daysLeft(Math.max(0, days ?? 0));

  return (
    <span
      className={`bp-chip ${TONES[urgency]}`}
      // The exact instant lives in the title so the chip stays compact but the precise
      // deadline is always one hover away.
      title={formatted ? `${formatted} (${timeZone})` : undefined}
    >
      {(urgency === "critical" || urgency === "passed") && (
        <span aria-hidden="true" className="text-[10px]">
          ●
        </span>
      )}
      {text}
    </span>
  );
}
