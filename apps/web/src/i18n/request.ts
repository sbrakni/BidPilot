import { getRequestConfig } from "next-intl/server";

import { routing, type Locale } from "./routing";

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = (routing.locales as readonly string[]).includes(requested ?? "")
    ? (requested as Locale)
    : routing.defaultLocale;

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
    // Dates and amounts display in the org's timezone (SPEC §16). Europe/Paris is the
    // default; a real org's setting overrides it once the profile is loaded.
    timeZone: "Europe/Paris",
  };
});
