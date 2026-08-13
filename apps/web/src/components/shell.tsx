/**
 * Application shell: left navigation per SPEC §20.1, in the order the spec lists it.
 *
 * The order is the product's argument: sourcing (Today, Opportunities) comes before
 * production (My tenders), and the library that proves claims sits beside both.
 */

import { getTranslations } from "next-intl/server";

import { Link } from "@/i18n/routing";
import { getCurrentUser } from "@/lib/session";

const NAV = [
  { key: "today", href: "/today" },
  { key: "opportunities", href: "/opportunities" },
  { key: "tenders", href: "/tenders" },
  { key: "library", href: "/library" },
  { key: "intel", href: "/intel" },
  { key: "settings", href: "/settings" },
] as const;

export async function AppShell({
  children,
  active,
}: {
  children: React.ReactNode;
  active: (typeof NAV)[number]["key"];
}) {
  const t = await getTranslations("nav");
  const tApp = await getTranslations("app");
  const user = await getCurrentUser();

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface md:flex">
        <div className="px-5 py-5">
          <Link href="/today" className="flex items-baseline gap-1.5">
            <span className="text-base font-semibold tracking-tight">{tApp("name")}</span>
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-accent" />
          </Link>
          <p className="mt-1 text-xs text-text-subtle">{tApp("tagline")}</p>
        </div>

        <nav className="flex-1 px-2" aria-label={tApp("name")}>
          <ul className="space-y-0.5">
            {NAV.map((item) => (
              <li key={item.key}>
                <Link
                  href={item.href}
                  aria-current={active === item.key ? "page" : undefined}
                  className={`block rounded-md px-3 py-1.5 text-sm transition-colors ${
                    active === item.key
                      ? "bg-accent-subtle font-medium text-accent"
                      : "text-text-muted hover:bg-surface-raised hover:text-text"
                  }`}
                >
                  {t(item.key)}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        {user && (
          <div className="border-t border-border px-5 py-4">
            <p className="text-xs text-text-subtle">{t("signedInAs")}</p>
            <p className="truncate text-sm font-medium">{user.name}</p>
            <p className="truncate text-xs text-text-muted">{user.org}</p>
            <Link href="/sign-in" className="mt-2 inline-block text-xs text-accent hover:underline">
              {t("switchOrg")}
            </Link>
          </div>
        )}
      </aside>

      <main className="min-w-0 flex-1">
        {/* Mobile navigation: responsive read-and-react, per §20.5. */}
        <nav className="flex gap-1 overflow-x-auto border-b border-border bg-surface px-3 py-2 md:hidden">
          {NAV.map((item) => (
            <Link
              key={item.key}
              href={item.href}
              className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${
                active === item.key ? "bg-accent-subtle font-medium text-accent" : "text-text-muted"
              }`}
            >
              {t(item.key)}
            </Link>
          ))}
        </nav>
        <div className="mx-auto max-w-6xl px-5 py-7">{children}</div>
      </main>
    </div>
  );
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="mb-6">
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-text-muted">{subtitle}</p>}
    </header>
  );
}

/** Empty states always teach the next action (§20.3). */
export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: { label: string; href: string };
}) {
  return (
    <div className="bp-card px-6 py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-text-muted">{body}</p>
      {action && (
        <Link
          href={action.href}
          className="mt-4 inline-block rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover"
        >
          {action.label}
        </Link>
      )}
    </div>
  );
}

/** Rendered when the API is unreachable, instead of a crash or a silent empty list. */
export function ApiUnavailable({ message, hint }: { message: string; hint: string }) {
  return (
    <div className="bp-card border-warning/40 bg-warning-subtle px-6 py-8 text-center">
      <p className="text-sm font-medium text-warning">{message}</p>
      <p className="mx-auto mt-2 max-w-md text-xs text-text-muted">{hint}</p>
    </div>
  );
}
