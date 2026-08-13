import { getTranslations, setRequestLocale } from "next-intl/server";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AppShell, PageHeader } from "@/components/shell";
import type { Locale } from "@/i18n/routing";
import { DEMO_USER_COOKIE } from "@/lib/api";
import { DEMO_USERS } from "@/lib/session";

/**
 * Demo organisation picker, standing in for Auth.js (SPEC §17.1).
 *
 * It only chooses which seeded user to act as; the API still verifies org membership, so
 * this cannot grant access to anything. The API refuses the header when
 * NODE_ENV=production, which is what keeps the shortcut from becoming a bypass.
 */
export default async function SignInPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const t = await getTranslations("signIn");

  async function chooose(formData: FormData) {
    "use server";
    const userId = String(formData.get("userId") ?? "");
    if (!DEMO_USERS.some((user) => user.id === userId)) return;
    const store = await cookies();
    store.set(DEMO_USER_COOKIE, userId, {
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24 * 7,
    });
    redirect("/today");
  }

  return (
    <AppShell active="settings">
      <PageHeader title={t("title")} subtitle={t("body")} />
      <ul className="grid gap-3 sm:grid-cols-3">
        {DEMO_USERS.map((user) => (
          <li key={user.id} className="bp-card p-4">
            <p className="text-sm font-medium">{user.name}</p>
            <p className="text-xs text-text-muted">
              {user.role} · {user.org}
            </p>
            <p className="mt-2 text-xs text-text-subtle">
              {locale === "fr" ? user.descriptionFr : user.descriptionEn}
            </p>
            <form action={chooose} className="mt-3">
              <input type="hidden" name="userId" value={user.id} />
              <button
                type="submit"
                className="w-full rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover"
              >
                {t("continueAs", { name: user.name })}
              </button>
            </form>
          </li>
        ))}
      </ul>
    </AppShell>
  );
}
