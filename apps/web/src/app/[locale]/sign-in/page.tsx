import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppShell, PageHeader } from "@/components/shell";
import { SignInForm } from "@/components/sign-in-form";
import { configuredOAuthProviders } from "@/auth";
import type { Locale } from "@/i18n/routing";

/**
 * Sign-in (SPEC §17.1): magic link, plus whichever OAuth providers this deployment configures.
 *
 * There is no password field and no registration step. Both are deliberate: §15.3 puts SIRET
 * entry first and targets five minutes to a live match (P5), and a password nobody set is a
 * password nobody can leak. A first magic link creates the account.
 */
export default async function SignInPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ error?: string }>;
}) {
  const { locale } = await params;
  const { error } = await searchParams;
  setRequestLocale(locale as Locale);
  const t = await getTranslations("signIn");

  return (
    <AppShell active="settings" signedOut>
      <PageHeader title={t("title")} subtitle={t("body")} />
      <div className="max-w-md">
        <SignInForm
          providers={configuredOAuthProviders()}
          error={error ? t("error") : null}
          labels={{
            email: t("email"),
            emailPlaceholder: t("emailPlaceholder"),
            submit: t("submit"),
            or: t("or"),
            google: t("google"),
            microsoft: t("microsoft"),
            noPassword: t("noPassword"),
          }}
        />
      </div>
    </AppShell>
  );
}
