import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppShell, EmptyState, PageHeader } from "@/components/shell";
import type { Locale } from "@/i18n/routing";

/**
 * Placeholder for the "settings" section of the information architecture (SPEC §20.1).
 *
 * It renders the section's empty state rather than 404ing: the navigation reflects the
 * product's shape, and this screen says plainly what is not built yet. See docs/STATUS.md.
 */
export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const t = await getTranslations("nav");

  return (
    <AppShell active="settings">
      <PageHeader title={t("settings")} />
      <EmptyState
        title={t("settings")}
        body={
          locale === "fr"
            ? "Cette section n'est pas encore construite. Voir docs/STATUS.md pour l'état d'avancement."
            : "This section is not built yet. See docs/STATUS.md for build state."
        }
        action={{ label: t("opportunities"), href: "/opportunities" }}
      />
    </AppShell>
  );
}
