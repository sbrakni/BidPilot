import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppShell, PageHeader } from "@/components/shell";
import type { Locale } from "@/i18n/routing";

/**
 * Where Auth.js sends someone after a magic link goes out.
 *
 * It names no address and confirms no account, for the same reason the sign-in action reports
 * nothing: this page is reachable by anyone who can type an email address into the form.
 */
export default async function CheckEmailPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const t = await getTranslations("signIn.checkEmail");

  return (
    <AppShell active="settings" signedOut>
      <PageHeader title={t("title")} />
      <div className="bp-card max-w-md p-5">
        <p className="text-sm text-text-muted">{t("body")}</p>
        <p className="mt-3 text-xs text-text-subtle">{t("expiry")}</p>
        <p className="mt-1 text-xs text-text-subtle">{t("spam")}</p>
      </div>
    </AppShell>
  );
}
