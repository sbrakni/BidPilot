/**
 * Auth.js route handler (SPEC §19: "POST /auth/… (Auth.js)").
 *
 * Deliberately outside `[locale]`: these endpoints are called by providers and by the client,
 * not visited by a person, so a locale segment would only add a way to get them wrong.
 */

import { handlers } from "@/auth";

export const { GET, POST } = handlers;
