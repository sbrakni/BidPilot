"use client";

import { useState, useTransition } from "react";

import { startEmailSignIn, startOAuthSignIn } from "@/app/actions";

type Labels = {
  email: string;
  emailPlaceholder: string;
  submit: string;
  or: string;
  google: string;
  microsoft: string;
  noPassword: string;
};

const buttonPrimary =
  "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50";

export function SignInForm({
  providers,
  error,
  labels,
}: {
  providers: Array<"google" | "microsoft-entra-id">;
  error: string | null;
  labels: Labels;
}) {
  const [email, setEmail] = useState("");
  const [pending, startTransition] = useTransition();

  return (
    <section className="bp-card p-5">
      {error && <p className="mb-3 text-xs text-danger">{error}</p>}

      <form
        action={(formData) => startTransition(() => void startEmailSignIn(formData))}
        className="space-y-2"
      >
        <label className="text-xs font-medium text-text-muted" htmlFor="email">
          {labels.email}
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder={labels.emailPlaceholder}
            className="min-w-56 flex-1 rounded-md border border-border bg-surface px-3 py-1.5 text-sm"
          />
          <button type="submit" disabled={pending || !email.trim()} className={buttonPrimary}>
            {labels.submit}
          </button>
        </div>
        <p className="text-[11px] text-text-subtle">{labels.noPassword}</p>
      </form>

      {providers.length > 0 && (
        <>
          <div className="my-4 flex items-center gap-3">
            <span className="h-px flex-1 bg-border" />
            <span className="text-[11px] uppercase tracking-wide text-text-subtle">{labels.or}</span>
            <span className="h-px flex-1 bg-border" />
          </div>
          <div className="flex flex-wrap gap-2">
            {providers.map((provider) => (
              <form
                key={provider}
                action={(formData) => startTransition(() => void startOAuthSignIn(formData))}
              >
                <input type="hidden" name="provider" value={provider} />
                <button
                  type="submit"
                  disabled={pending}
                  className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-muted hover:bg-surface disabled:opacity-50"
                >
                  {provider === "google" ? labels.google : labels.microsoft}
                </button>
              </form>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
