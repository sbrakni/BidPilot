/**
 * Demo session, standing in for Auth.js (SPEC §17.1) until it lands.
 *
 * It stores only a seeded user id in a cookie and forwards it to the API, which still
 * verifies org membership itself - so this shortcut cannot widen anyone's access, it only
 * skips proving *who* you are. The API refuses the header entirely when
 * NODE_ENV=production, so this cannot become a production authentication bypass.
 */

import { cookies } from "next/headers";

import { DEMO_USER_COOKIE } from "./api";

/** The seeded personas from SPEC §3.1. Kept in sync with packages/db/src/seed.ts. */
export const DEMO_USERS = [
  {
    id: "user_lea",
    name: "Léa Marchand",
    role: "CEO",
    org: "Néosys Conseil",
    descriptionFr: "ESN de 35 personnes, infogérance et développement, Île-de-France.",
    descriptionEn: "35-person IT services company, Île-de-France.",
  },
  {
    id: "user_marc",
    name: "Marc Dubois",
    role: "Bid manager",
    org: "Atelier Compétences",
    descriptionFr: "Organisme de formation certifié Qualiopi, intervention nationale.",
    descriptionEn: "Qualiopi-certified training provider, national coverage.",
  },
  {
    id: "user_sofia",
    name: "Sofia Perrin",
    role: "Ops director",
    org: "Provence Facility Services",
    descriptionFr: "Maintenance multitechnique et propreté, 80 collaborateurs, PACA.",
    descriptionEn: "Multi-technical maintenance and cleaning, 80 staff, PACA.",
  },
] as const;

export type DemoUser = (typeof DEMO_USERS)[number];

export async function getCurrentUserId(): Promise<string | null> {
  const store = await cookies();
  return store.get(DEMO_USER_COOKIE)?.value ?? null;
}

export async function getCurrentUser(): Promise<DemoUser | null> {
  const id = await getCurrentUserId();
  return DEMO_USERS.find((user) => user.id === id) ?? null;
}
