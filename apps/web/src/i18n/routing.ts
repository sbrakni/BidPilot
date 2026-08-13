import { defineRouting } from "next-intl/routing";
import { createNavigation } from "next-intl/navigation";

/**
 * i18n routing (SPEC §4.3, §20.6).
 *
 * French is the default because the launch geographies are FR/BE/LU and the domain
 * vocabulary is French. English ships from day one anyway: Belgium and Luxembourg are
 * mixed-language in practice (§24.6).
 */
export const routing = defineRouting({
  locales: ["fr", "en"],
  defaultLocale: "fr",
  // The default locale is not prefixed, so /today is French and /en/today is English.
  localePrefix: "as-needed",
});

export type Locale = (typeof routing.locales)[number];

export const { Link, redirect, usePathname, useRouter, getPathname } = createNavigation(routing);
