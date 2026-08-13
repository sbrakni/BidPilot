/**
 * Match inbox card (SPEC §20.2).
 *
 * "card list: score ring with breakdown popover, title, buyer, amount, CPV chips, deadline
 * chip (color by urgency), source badges, eliminatory pre-flags, actions."
 *
 * The source badges matter more than they look: they are the visible proof of §6.7's
 * deduplication - one card, every place the tender was published. Without them, a user who
 * knows the tender is on both TED and BOAMP would reasonably suspect we had missed one.
 */

import { ScoreBreakdownList, ScoreRing } from "@/components/score";
import { DeadlineChip } from "@/components/deadline";
import type { MatchCard as MatchCardData } from "@/lib/api";
import { formatAmount, formatDate, sourceLabel } from "@/lib/format";

import { MatchActions } from "./match-actions";

export function MatchCard({
  match,
  locale,
  timeZone,
  labels,
}: {
  match: MatchCardData;
  locale: string;
  timeZone: string;
  labels: {
    breakdown: string;
    sources: string;
    amountUnknown: string;
    published: string;
    deadline: { none: string; passed: string; daysLeft: (days: number) => string };
    actions: { shortlist: string; dismiss: string; pursue: string; open: string };
    dismissReasons: Record<string, string>;
  };
}) {
  const { notice } = match;
  const amount = formatAmount(notice.amountEst, locale, notice.currency ?? "EUR");
  const sources = Array.isArray(notice.sourceRefs) ? notice.sourceRefs : [];
  const noticeUrl = notice.urls?.notice ?? sources[0]?.url ?? null;

  return (
    <li className="bp-card p-4">
      <div className="flex gap-4">
        <ScoreRing score={match.score} />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <h2 className="line-clamp-2 min-w-0 text-sm font-medium leading-snug" title={notice.title}>
              {noticeUrl ? (
                <a
                  href={noticeUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="hover:text-accent hover:underline"
                >
                  {notice.title}
                </a>
              ) : (
                notice.title
              )}
            </h2>
            <DeadlineChip
              deadline={notice.deadlineAt}
              locale={locale}
              timeZone={timeZone}
              labels={labels.deadline}
            />
          </div>

          <p className="mt-1 truncate text-xs text-text-muted">
            {notice.buyerName ?? "—"}
            {amount ? ` · ${amount}` : ` · ${labels.amountUnknown}`}
            {notice.publishedAt ? ` · ${labels.published} ${formatDate(notice.publishedAt, locale, timeZone)}` : ""}
          </p>

          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {notice.cpv.slice(0, 3).map((code) => (
              <span key={code} className="bp-chip bg-surface text-text-muted">
                {code}
              </span>
            ))}
            {notice.nuts.slice(0, 2).map((code) => (
              <span key={code} className="bp-chip bg-surface text-text-subtle">
                {code}
              </span>
            ))}
            {sources.length > 0 && (
              <span className="ml-1 text-[11px] text-text-subtle">
                {labels.sources}{" "}
                {sources.map((ref) => sourceLabel(ref.source)).join(", ")}
              </span>
            )}
          </div>

          {/* A <details> popover keeps the breakdown one keystroke away without a client
              bundle, and stays fully keyboard-navigable (§16 accessibility). */}
          <details className="group mt-3">
            <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-xs text-accent hover:underline">
              <span aria-hidden="true" className="transition-transform group-open:rotate-90">
                ›
              </span>
              {labels.breakdown}
            </summary>
            <div className="mt-3 rounded-md border border-border bg-surface p-3">
              <ScoreBreakdownList breakdown={match.breakdown} title={labels.breakdown} />
            </div>
          </details>

          <MatchActions
            matchId={match.id}
            state={match.state}
            labels={labels.actions}
            dismissReasons={labels.dismissReasons}
          />
        </div>
      </div>
    </li>
  );
}
