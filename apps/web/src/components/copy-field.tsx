"use client";

import { useState } from "react";

/**
 * A read-only value with a copy button.
 *
 * The value is in an input rather than a `<p>` so it can still be selected and copied by hand
 * when the clipboard API is unavailable - it is blocked on insecure origins, which includes
 * every plain-HTTP deployment. Falling back to "select the text yourself" beats a button that
 * silently does nothing.
 */
export function CopyField({
  value,
  label,
  copied,
}: {
  value: string;
  label: string;
  copied: string;
}) {
  const [done, setDone] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
      setTimeout(() => setDone(false), 2000);
    } catch {
      // No clipboard permission: the value is selectable, so there is still a way through.
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        readOnly
        value={value}
        aria-label={label}
        onFocus={(event) => event.currentTarget.select()}
        className="min-w-72 flex-1 rounded-md border border-border bg-surface px-3 py-1.5 font-mono text-xs"
      />
      <button
        type="button"
        onClick={copy}
        className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-text-muted hover:bg-surface"
      >
        {done ? copied : label}
      </button>
    </div>
  );
}
