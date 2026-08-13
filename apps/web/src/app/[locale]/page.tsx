import { redirect } from "@/i18n/routing";

/** "Aujourd'hui" is the home screen (SPEC §20.1): what needs attention today. */
export default async function Home({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  redirect({ href: "/today", locale });
}
