import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppShell, EmptyState, PageHeader } from "@/components/shell";
import type { Locale } from "@/i18n/routing";
import { api, isUnreachable, type EvidenceCard, type LibraryPage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { getCurrentUserId } from "@/lib/session";

/**
 * The evidence vault (SPEC §7.2), and the health widget §7.4 asks for.
 *
 * The counters lead, because this screen's job is not to list documents - it is to answer "can
 * we still bid". An expired URSSAF attestation is an eliminatory condition waiting to happen,
 * and §7.2 has eligibility read this status, so the list is ordered by what expires soonest
 * rather than by what was added last.
 *
 * Red is used here, which the design tokens reserve for eliminatory and deadline danger (§20.3).
 * An expired proof is exactly that: it is the thing that gets a bid rejected unread.
 */
export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const t = await getTranslations("library");
  const tSignIn = await getTranslations("signIn");
  const tEmpty = await getTranslations("empty.library");

  const userId = await getCurrentUserId();
  if (!userId) {
    return (
      <AppShell active="library">
        <PageHeader title={t("title")} />
        <EmptyState
          title={tSignIn("title")}
          body={tSignIn("body")}
          action={{ label: tSignIn("title"), href: "/sign-in" }}
        />
      </AppShell>
    );
  }

  let library: LibraryPage | null = null;
  let unreachable = false;
  try {
    library = await api.library();
  } catch (error) {
    unreachable = isUnreachable(error);
  }

  const health = library?.health;
  const evidence = library?.data ?? [];

  return (
    <AppShell active="library">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      {health && health.total > 0 && (
        <section className="mb-6 grid gap-3 sm:grid-cols-3" aria-label={t("vaultHealth")}>
          <Counter label={t("statuses.valid")} value={health.valid} tone="neutral" />
          <Counter label={t("statuses.expiring")} value={health.expiring} tone="warning" />
          <Counter label={t("statuses.expired")} value={health.expired} tone="danger" />
        </section>
      )}

      {evidence.length === 0 ? (
        <EmptyState
          title={unreachable ? tSignIn("error") : tEmpty("title")}
          body={unreachable ? tSignIn("error") : tEmpty("body")}
          action={{ label: tEmpty("action"), href: "/settings" }}
        />
      ) : (
        <ul className="space-y-2">
          {evidence.map((item) => (
            <li key={item.id} className="bp-card p-4">
              <EvidenceRow
                item={item}
                locale={locale}
                labels={{
                  kind: t(`kinds.${item.kind}`),
                  status: t(`statuses.${item.status}`),
                  noExpiry: t("noExpiry"),
                  expiresIn: (days: number) => t("expiresIn", { days }),
                  expiredFor: (days: number) => t("expiredFor", { days }),
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "neutral" | "warning" | "danger";
}) {
  const toneClass =
    tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-text";
  return (
    <div className="bp-card p-4">
      <p className="text-xs text-text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${value > 0 ? toneClass : "text-text-subtle"}`}>
        {value}
      </p>
    </div>
  );
}

function EvidenceRow({
  item,
  locale,
  labels,
}: {
  item: EvidenceCard;
  locale: string;
  labels: {
    kind: string;
    status: string;
    noExpiry: string;
    expiresIn: (days: number) => string;
    expiredFor: (days: number) => string;
  };
}) {
  const days = item.daysUntilExpiry;
  const expiry =
    days === null
      ? labels.noExpiry
      : days < 0
        ? labels.expiredFor(Math.abs(days))
        : labels.expiresIn(days);

  const tone =
    item.status === "expired"
      ? "bg-danger-subtle text-danger"
      : item.status === "expiring"
        ? "bg-warning-subtle text-warning"
        : "bg-surface text-text-muted";

  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={item.title}>
          {item.title}
        </p>
        <p className="mt-0.5 text-xs text-text-muted">
          {[labels.kind, item.issuer, formatDate(item.issuedAt, locale)].filter(Boolean).join(" · ")}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`bp-chip ${tone}`}>{labels.status}</span>
        <span className="text-xs text-text-subtle">{expiry}</span>
      </div>
    </div>
  );
}
