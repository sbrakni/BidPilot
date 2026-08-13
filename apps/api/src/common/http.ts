/**
 * Outbound HTTP for the API.
 *
 * Node's global `fetch` ignores `HTTPS_PROXY` unless `NODE_USE_ENV_PROXY=1` is set *before the
 * process starts* - too late to fix from inside `main.ts`, as trying it demonstrated. Most managed
 * hosting sits behind an egress proxy, so relying on an operator remembering an env flag would
 * mean the SIRET bootstrap silently returns "registry unavailable" in production while working
 * perfectly in development.
 *
 * So the proxy is configured explicitly here, and the configured fetch is passed to the code that
 * needs it rather than installed globally: a global dispatcher would quietly change the behaviour
 * of every other caller, including tests.
 */

import { EnvHttpProxyAgent, fetch as undiciFetch } from "undici";

/** Reads HTTP_PROXY / HTTPS_PROXY / NO_PROXY from the environment, honouring exclusions. */
const proxyAgent = new EnvHttpProxyAgent();

const usingProxy = Boolean(
  process.env.HTTPS_PROXY ?? process.env.https_proxy ?? process.env.HTTP_PROXY ?? process.env.http_proxy,
);

/**
 * `fetch` that respects the environment's proxy settings.
 *
 * Signature-compatible with the global `fetch`, so it can be injected wherever one is expected.
 */
export const proxyAwareFetch: typeof fetch = (async (input: unknown, init?: unknown) => {
  if (!usingProxy) {
    return (globalThis.fetch as (...args: unknown[]) => Promise<unknown>)(input, init);
  }
  const url = typeof input === "string" ? input : String((input as { url?: string })?.url ?? input);
  return undiciFetch(url, { ...(init as Record<string, unknown>), dispatcher: proxyAgent });
}) as unknown as typeof fetch;
