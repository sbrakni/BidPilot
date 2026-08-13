"use client";

/**
 * Inbox triage actions (SPEC §8.3, §20.2).
 *
 * Dismissal always asks for a reason. That is not friction for its own sake: the reason is
 * the training signal for §8.3's weight-tuning loop, and it is what keeps a dismissed notice
 * out of future digests. A dismissal without a reason teaches the product nothing.
 */

import { useState, useTransition } from "react";

import { dismissMatch, pursueMatch, shortlistMatch } from "@/app/actions";

type State = "new" | "shortlisted" | "dismissed" | "pursued";

export function MatchActions({
  matchId,
  state,
  labels,
  dismissReasons,
}: {
  matchId: string;
  state: State;
  labels: { shortlist: string; dismiss: string; pursue: string; open: string };
  dismissReasons: Record<string, string>;
}) {
  const [pending, startTransition] = useTransition();
  const [choosingReason, setChoosingReason] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState<State>(state);

  function run(action: () => Promise<{ ok: boolean; error?: string }>, next: State) {
    setError(null);
    startTransition(async () => {
      const result = await action();
      if (result.ok) {
        setCurrent(next);
        setChoosingReason(false);
      } else {
        setError(result.error ?? "error");
      }
    });
  }

  const buttonBase =
    "rounded-md px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50";

  if (choosingReason) {
    return (
      <div className="mt-3 space-y-2">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(dismissReasons).map(([value, label]) => (
            <button
              key={value}
              type="button"
              disabled={pending}
              onClick={() => run(() => dismissMatch(matchId, value), "dismissed")}
              className={`${buttonBase} border border-border text-text-muted hover:bg-surface`}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setChoosingReason(false)}
          className="text-xs text-text-subtle hover:underline"
        >
          ←
        </button>
      </div>
    );
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        disabled={pending || current === "shortlisted"}
        onClick={() => run(() => shortlistMatch(matchId), "shortlisted")}
        className={`${buttonBase} border border-border ${
          current === "shortlisted" ? "bg-accent-subtle text-accent" : "text-text-muted hover:bg-surface"
        }`}
      >
        {labels.shortlist}
      </button>

      <button
        type="button"
        disabled={pending}
        onClick={() => setChoosingReason(true)}
        className={`${buttonBase} border border-border ${
          current === "dismissed" ? "bg-surface text-text-subtle line-through" : "text-text-muted hover:bg-surface"
        }`}
      >
        {labels.dismiss}
      </button>

      {/* P2: pursuing opens analysis and decision, never the response studio directly. */}
      <button
        type="button"
        disabled={pending || current === "pursued"}
        onClick={() => run(() => pursueMatch(matchId), "pursued")}
        className={`${buttonBase} ${
          current === "pursued"
            ? "bg-success-subtle text-success"
            : "bg-accent text-white hover:bg-accent-hover"
        }`}
      >
        {labels.pursue}
      </button>

      {error && <span className="text-xs text-danger">{error}</span>}
    </div>
  );
}
