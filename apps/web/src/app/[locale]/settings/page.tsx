import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppShell, EmptyState, PageHeader } from "@/components/shell";
import { CopyField } from "@/components/copy-field";
import type { Locale } from "@/i18n/routing";
import { api, isUnreachable, type OrgSummary } from "@/lib/api";
import { getCurrentUserId } from "@/lib/session";

/**
 * Settings (SPEC §20.1).
 *
 * Only one thing lives here so far, and it earns the screen: the org's inbound address (§6.5).
 * The email connector is worthless if nobody knows where to send mail, so the address has to be
 * somewhere a user can find and copy - a feature that works but is undiscoverable has not
 * shipped.
 */
export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const t = await getTranslations("nav");
  const tInbound = await getTranslations("settings.inbound");

  const userId = await getCurrentUserId();
  let org: OrgSummary | null = null;
  let unreachable = false;
  if (userId) {
    try {
      org = await api.org();
    } catch (error) {
      unreachable = isUnreachable(error);
    }
  }

  return (
    <AppShell active="settings">
      <PageHeader title={t("settings")} />

      <section className="bp-card max-w-2xl p-5">
        <h2 className="text-sm font-medium">{tInbound("title")}</h2>
        <p className="mt-1 text-sm text-text-muted">{tInbound("body")}</p>

        {org?.inboundEmail ? (
          <>
            <div className="mt-4">
              <CopyField value={org.inboundEmail} label={tInbound("copy")} copied={tInbound("copied")} />
            </div>
            <ol className="mt-4 list-decimal space-y-1 pl-5 text-xs text-text-muted">
              <li>{tInbound("step1")}</li>
              <li>{tInbound("step2")}</li>
              <li>{tInbound("step3")}</li>
            </ol>
            <p className="mt-3 text-[11px] text-text-subtle">{tInbound("privacy")}</p>
          </>
        ) : (
          <p className="mt-4 text-xs text-warning">
            {unreachable || !userId ? tInbound("unavailable") : tInbound("notConfigured")}
          </p>
        )}
      </section>

      <div className="mt-6 max-w-2xl">
        <EmptyState
          title={t("settings")}
          body={
            locale === "fr"
              ? "Le reste des réglages n'est pas encore construit. Voir docs/STATUS.md."
              : "The rest of settings is not built yet. See docs/STATUS.md."
          }
          action={{ label: t("opportunities"), href: "/opportunities" }}
        />
      </div>
    </AppShell>
  );
}
