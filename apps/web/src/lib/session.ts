/**
 * The acting user, from the Auth.js session (SPEC §17.1).
 *
 * Every caller of this used to get an id out of a cookie that nothing had verified. Now the
 * session is a row in the database, so "who is this" is a lookup rather than an assertion, and
 * deleting the row signs the person out immediately.
 */

import { auth } from "@/auth";

export type CurrentUser = {
  id: string;
  email: string | null;
  name: string | null;
  image: string | null;
};

export async function getCurrentUser(): Promise<CurrentUser | null> {
  const session = await auth();
  const user = session?.user;
  if (!user?.id) return null;
  return {
    id: user.id,
    email: user.email ?? null,
    name: user.name ?? null,
    image: user.image ?? null,
  };
}

export async function getCurrentUserId(): Promise<string | null> {
  return (await getCurrentUser())?.id ?? null;
}

/** A display name that is never empty: the local part of the address is better than "null". */
export function displayName(user: CurrentUser): string {
  return user.name?.trim() || user.email?.split("@")[0] || "—";
}
