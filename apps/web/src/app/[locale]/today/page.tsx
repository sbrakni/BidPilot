/**
 * "Aujourd'hui" (SPEC §20.1) - the daily triage surface.
 *
 * Deliberately a small set of counters plus the top matches: the point is to answer "what
 * needs me today?" in one glance, not to be a dashboard. Deadlines get their own tile
 * because a missed one is the worst failure the product can have (P3).
 */

import { getTranslations, setRequestLocale } from "next-intl/server";

import { ApiUnavailable, AppShell, EmptyState, PageHeader } from "@/components/shell";
import { MatchCard } from "@/components/match-card";
import { Link, type Locale } from "@/i18n/routing";
import { api, isUnreachable, type MatchPage, type OrgSummary } from "@/lib/api";
import { getCurrentUserId } from "@/lib/session";
import { daysUntil } from "@/lib/format";

function Tile({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "danger" | "warning" }) {
  const toneClass =
    tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-text";
  return (
    <div className="bp-card px-4 py-3">
      <p className="text-xs text-text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</p>
    </div>
  );
}

export default async function TodayPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);

  const t = await getTranslations("today");
  const tNotice = await getTranslations("notice");
  const tOpp = await getTranslations("opportunities");
  const tEmpty = await getTranslations("empty.matches");
  const tSignIn = await getTranslations("signIn");

  const userId = await getCurrentUserId();
  if (!userId) {
    return (
      <AppShell active="today">
        <PageHeader title={t("title")} />
        <EmptyState title={tSignIn("title")} body={tSignIn("body")} action={{ label: tSignIn("title"), href: "/sign-in" }} />
      </AppShell>
    );
  }

  let org: OrgSummary | null = null;
  let page: MatchPage | null = null;
  let failure: string | null = null;
  try {
    [org, page] = await Promise.all([api.org(), api.matches({ state: "new", limit: 5 })]);
  } catch (error) {
    failure = isUnreachable(error) ? "unreachable" : (error as Error).message;
  }

  if (failure) {
    return (
      <AppShell active="today">
        <PageHeader title={t("title")} subtitle={t("subtitle")} />
        <ApiUnavailable
          message={failure === "unreachable" ? "API indisponible" : failure}
          hint="Vérifiez que l'API tourne (pnpm dev) et que la base est migrée puis seedée."
        />
      </AppShell>
    );
  }

  const matches = page?.data ?? [];
  // "Deadlines this week" is computed from what we hold, not stored: the countdown must never
  // be able to go stale (P3).
  const dueThisWeek = matches.filter((match) => {
    const days = daysUntil(match.notice.deadlineAt);
    return days !== null && days >= 0 && days <= 7;
  }).length;

  return (
    <AppShell active="today">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label={t("newMatches")} value={String(org?.counters.newMatches ?? 0)} />
        <Tile label={t("deadlinesThisWeek")} value={String(dueThisWeek)} tone={dueThisWeek > 0 ? "warning" : "neutral"} />
        <Tile
          label={t("vaultAlerts")}
          value={String(org?.counters.evidenceNeedingAttention ?? 0)}
          tone={(org?.counters.evidenceNeedingAttention ?? 0) > 0 ? "warning" : "neutral"}
        />
        <Tile label={t("decisionsPending")} value="0" />
      </div>

      <section>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-sm font-medium">{t("topMatches")}</h2>
          <Link href="/opportunities" className="text-xs text-accent hover:underline">
            {t("seeAll")}
          </Link>
        </div>

        {matches.length === 0 ? (
          <EmptyState title={tEmpty("title")} body={tEmpty("body")} action={{ label: tEmpty("action"), href: "/settings" }} />
        ) : (
          <ul className="space-y-3">
            {matches.map((match) => (
              <MatchCard
                key={match.id}
                match={match}
                locale={locale}
                timeZone={org?.tz ?? "Europe/Paris"}
                labels={{
                  breakdown: tOpp("breakdown"),
                  sources: tOpp("sourcesLabel"),
                  amountUnknown: tNotice("amountUnknown"),
                  published: tNotice("published"),
                  deadline: {
                    none: tNotice("noDeadline"),
                    passed: tNotice("deadlinePassed"),
                    daysLeft: (days: number) => tNotice("daysLeft", { days }),
                  },
                  actions: {
                    shortlist: tOpp("actions.shortlist"),
                    dismiss: tOpp("actions.dismiss"),
                    pursue: tOpp("actions.pursue"),
                    open: tOpp("actions.open"),
                  },
                  dismissReasons: {
                    too_big: tOpp("dismissReasons.too_big"),
                    too_small: tOpp("dismissReasons.too_small"),
                    wrong_activity: tOpp("dismissReasons.wrong_activity"),
                    no_time: tOpp("dismissReasons.no_time"),
                    bad_buyer: tOpp("dismissReasons.bad_buyer"),
                    other: tOpp("dismissReasons.other"),
                  },
                }}
              />
            ))}
          </ul>
        )}
      </section>
    </AppShell>
  );
}
