import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppShell, PageHeader } from "@/components/shell";
import { OnboardingWizard } from "@/components/onboarding-wizard";
import type { Locale } from "@/i18n/routing";
import { getCurrentUserId } from "@/lib/session";
import { redirect } from "@/i18n/routing";

/**
 * Onboarding wizard (SPEC §15.3) - the scripted path to the P5 promise: SIREN in, live matching
 * tenders out, in under five minutes.
 *
 * Three steps rather than the spec's seven, on purpose. The spec's steps 1 (signup) and 7 (digest
 * opt-in) belong to Auth.js and settings respectively, and its "instant 20 matches" step is the
 * inbox we already have. What is left is the part that only onboarding can do: identity, then
 * scope, then hand the user to their own inbox.
 */
export default async function OnboardingPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);

  const t = await getTranslations("onboarding");
  const userId = await getCurrentUserId();
  if (!userId) {
    redirect({ href: "/sign-in", locale });
  }

  return (
    <AppShell active="settings">
      <PageHeader title={t("title")} />
      <OnboardingWizard
        labels={{
          steps: [1, 2, 3].map((current) => t("step", { current, total: 3 })),
          siret: {
            title: t("siret.title"),
            body: t("siret.body"),
            placeholder: t("siret.placeholder"),
            submit: t("siret.submit"),
            manual: t("siret.manual"),
            notFound: t("siret.notFound"),
            unavailable: t("siret.unavailable"),
            invalid: t("siret.invalid"),
          },
          confirm: {
            title: t("confirm.title"),
            body: t("confirm.body"),
            legalName: t("confirm.legalName"),
            naf: t("confirm.naf"),
            headcountBand: t("confirm.headcountBand"),
            location: t("confirm.location"),
            unknown: t("confirm.unknown"),
            submit: t("confirm.submit"),
          },
          scope: {
            title: t("scope.title"),
            body: t("scope.body"),
            sectorPack: t("scope.sectorPack"),
            zones: t("scope.zones"),
            national: t("scope.national"),
            revenue: t("scope.revenue"),
            revenueHint: t("scope.revenueHint"),
            submit: t("scope.submit"),
          },
        }}
      />
    </AppShell>
  );
}
