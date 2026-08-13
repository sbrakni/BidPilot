import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppShell, EmptyState, PageHeader } from "@/components/shell";
import { DeadlineChip } from "@/components/deadline";
import type { Locale } from "@/i18n/routing";
import { api, isUnreachable, type TenderCard, type TenderList } from "@/lib/api";
import { formatAmount } from "@/lib/format";
import { getCurrentUserId } from "@/lib/session";

/**
 * "Mes AO" - the org's live consultations, by stage (SPEC §20.1).
 *
 * Two features wrote here before anything could read it: `pursue` on a match, and the email
 * connector (§6.5). A row nobody can see has not shipped, which is what this screen fixes.
 *
 * Ordered by deadline rather than grouped rigidly by stage, with the stage as a chip. P3 is why:
 * the question this screen answers is "what closes soonest", and grouping by stage buries a
 * Friday deadline under a column heading.
 */

/** The §20.1 order: work moves left to right, so the counters read in that order too. */
const STAGES = ["analysis", "decision", "response", "submitted", "closed"] as const;

const STAGE_TONES: Record<string, string> = {
  analysis: "bg-surface text-text-muted",
  decision: "bg-accent-subtle text-accent",
  response: "bg-accent-subtle text-accent",
  submitted: "bg-surface text-text-muted",
  closed: "bg-surface text-text-subtle",
};

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const t = await getTranslations("tenders");
  const tNotice = await getTranslations("notice");
  const tEmpty = await getTranslations("empty.tenders");
  const tSignIn = await getTranslations("signIn");
  const tOpportunities = await getTranslations("opportunities");

  const userId = await getCurrentUserId();
  if (!userId) {
    return (
      <AppShell active="tenders">
        <PageHeader title={t("title")} />
        <EmptyState
          title={tSignIn("title")}
          body={tSignIn("body")}
          action={{ label: tSignIn("title"), href: "/sign-in" }}
        />
      </AppShell>
    );
  }

  let list: TenderList | null = null;
  let unreachable = false;
  try {
    list = await api.tenders();
  } catch (error) {
    unreachable = isUnreachable(error);
  }

  const tenders = list?.data ?? [];

  return (
    <AppShell active="tenders">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      {tenders.length > 0 && (
        <ul className="mb-5 flex flex-wrap gap-1.5" aria-label={t("subtitle")}>
          {STAGES.filter((stage) => (list?.countsByStage[stage] ?? 0) > 0).map((stage) => (
            <li key={stage} className={`bp-chip ${STAGE_TONES[stage]}`}>
              {t(`stages.${stage}`)} · {list?.countsByStage[stage]}
            </li>
          ))}
        </ul>
      )}

      {tenders.length === 0 ? (
        <EmptyState
          title={unreachable ? tSignIn("error") : tEmpty("title")}
          body={unreachable ? tSignIn("error") : tEmpty("body")}
          action={{ label: tEmpty("action"), href: "/opportunities" }}
        />
      ) : (
        <ul className="space-y-2">
          {tenders.map((tender) => (
            <li key={tender.id} className="bp-card p-4">
              <TenderRow
                tender={tender}
                locale={locale}
                labels={{
                  stage: t(`stages.${tender.stage}`),
                  none: tNotice("noDeadline"),
                  passed: tNotice("deadlinePassed"),
                  amountUnknown: tNotice("amountUnknown"),
                  open: tOpportunities("actions.open"),
                  viaEmail: t("viaEmail"),
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}

function TenderRow({
  tender,
  locale,
  labels,
}: {
  tender: TenderCard;
  locale: string;
  labels: {
    stage: string;
    none: string;
    passed: string;
    amountUnknown: string;
    open: string;
    viaEmail: string;
  };
}) {
  const amount = formatAmount(
    tender.notice?.amountEst ?? null,
    locale,
    tender.notice?.currency ?? "EUR",
  );

  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <p className="line-clamp-2 text-sm font-medium" title={tender.title}>
          {tender.title}
        </p>
        <p className="mt-0.5 text-xs text-text-muted">
          {[tender.notice?.buyerName, amount ?? labels.amountUnknown].filter(Boolean).join(" · ")}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className={`bp-chip ${STAGE_TONES[tender.stage]}`}>{labels.stage}</span>
          {/* Where a candidate came from is part of judging it: an emailed link has had no
              matching applied, so the user is the first filter (P6). */}
          {tender.origin === "email" && (
            <span className="bp-chip bg-surface text-text-subtle">{labels.viaEmail}</span>
          )}
          {tender.sourceUrl && (
            <a
              href={tender.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-accent hover:underline"
            >
              {labels.open}
            </a>
          )}
        </div>
      </div>
      <DeadlineChip
        deadline={tender.deadlineAt}
        locale={locale}
        labels={{
          none: labels.none,
          passed: labels.passed,
          daysLeft: (days) => `${days} j`,
        }}
      />
    </div>
  );
}
