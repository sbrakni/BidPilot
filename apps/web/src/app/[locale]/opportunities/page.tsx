/**
 * Match inbox (SPEC §20.2 screen 2) - the screen users live in.
 *
 * Everything on a card is there to support one decision: is this worth my next hour? So the
 * score is never shown without its breakdown, the deadline is never shown without its
 * urgency, and the source badges show which portals this one card replaces.
 */

import { getTranslations, setRequestLocale } from "next-intl/server";

import { ApiUnavailable, AppShell, EmptyState, PageHeader } from "@/components/shell";
import { MatchCard } from "@/components/match-card";
import { Link } from "@/i18n/routing";
import type { Locale } from "@/i18n/routing";
import { api, isUnreachable, type MatchPage } from "@/lib/api";
import { getCurrentUserId } from "@/lib/session";

const STATES = ["all", "new", "shortlisted", "pursued", "dismissed"] as const;

export default async function OpportunitiesPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ state?: string; minScore?: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const { state = "all", minScore } = await searchParams;

  const t = await getTranslations("opportunities");
  const tNotice = await getTranslations("notice");
  const tEmpty = await getTranslations("empty.matches");
  const tSignIn = await getTranslations("signIn");

  const userId = await getCurrentUserId();
  if (!userId) {
    return (
      <AppShell active="opportunities">
        <PageHeader title={t("title")} />
        <EmptyState
          title={tSignIn("title")}
          body={tSignIn("body")}
          action={{ label: tSignIn("title"), href: "/sign-in" }}
        />
      </AppShell>
    );
  }

  let page: MatchPage | null = null;
  let failure: string | null = null;
  try {
    page = await api.matches({
      state,
      minScore: minScore ? Number(minScore) : undefined,
      limit: 25,
    });
  } catch (error) {
    failure = isUnreachable(error) ? "unreachable" : (error as Error).message;
  }

  const matches = page?.data ?? [];
  const labels = {
    breakdown: t("breakdown"),
    sources: t("sourcesLabel"),
    amountUnknown: tNotice("amountUnknown"),
    published: tNotice("published"),
    deadline: {
      none: tNotice("noDeadline"),
      passed: tNotice("deadlinePassed"),
      daysLeft: (days: number) => tNotice("daysLeft", { days }),
    },
    actions: {
      shortlist: t("actions.shortlist"),
      dismiss: t("actions.dismiss"),
      pursue: t("actions.pursue"),
      open: t("actions.open"),
    },
    dismissReasons: {
      too_big: t("dismissReasons.too_big"),
      too_small: t("dismissReasons.too_small"),
      wrong_activity: t("dismissReasons.wrong_activity"),
      no_time: t("dismissReasons.no_time"),
      bad_buyer: t("dismissReasons.bad_buyer"),
      other: t("dismissReasons.other"),
    },
  };

  return (
    <AppShell active="opportunities">
      <PageHeader title={t("title")} subtitle={t("subtitle", { count: matches.length })} />

      <nav className="mb-4 flex flex-wrap gap-1.5" aria-label={t("filters.all")}>
        {STATES.map((value) => (
          <Link
            key={value}
            href={value === "all" ? "/opportunities" : `/opportunities?state=${value}`}
            className={`rounded-md px-2.5 py-1 text-xs font-medium ${
              state === value
                ? "bg-accent-subtle text-accent"
                : "border border-border text-text-muted hover:bg-surface"
            }`}
          >
            {t(`filters.${value}`)}
          </Link>
        ))}
      </nav>

      {failure ? (
        <ApiUnavailable
          message={failure === "unreachable" ? "API indisponible" : failure}
          hint="Vérifiez que l'API tourne (pnpm dev) et que la base est migrée puis seedée."
        />
      ) : matches.length === 0 ? (
        <EmptyState title={tEmpty("title")} body={tEmpty("body")} action={{ label: tEmpty("action"), href: "/settings" }} />
      ) : (
        <>
          <ul className="space-y-3">
            {matches.map((match) => (
              <MatchCard
                key={match.id}
                match={match}
                locale={locale}
                timeZone="Europe/Paris"
                labels={labels}
              />
            ))}
          </ul>
          {page?.nextCursor && (
            <p className="mt-4 text-center text-xs text-text-subtle">
              {/* Cursor pagination all the way down (§19). */}
              <Link
                href={`/opportunities?state=${state}&cursor=${page.nextCursor}`}
                className="text-accent hover:underline"
              >
                {t("seeAll", { defaultValue: "→" })}
              </Link>
            </p>
          )}
        </>
      )}

      <p className="mt-6 text-xs text-text-subtle">{t("keyboardHint")}</p>
    </AppShell>
  );
}
