/**
 * Match score display (SPEC §8.2, §20.2).
 *
 * "Every score exposes its breakdown. No opaque numbers." (P4)
 *
 * So the ring is never rendered alone: the factor bars sit with it, each labelled and each
 * carrying the detail string the scoring engine produced. A user who disagrees with a score
 * can see exactly which factor to argue with, which is what makes the number trustworthy
 * rather than merely present.
 */

import type { ScoreBreakdown, ScoreFactor } from "@/lib/api";

/** Bands are visual only - they never change a score, and the number is always shown. */
function scoreTone(score: number): { ring: string; text: string } {
  if (score >= 80) return { ring: "stroke-success", text: "text-success" };
  if (score >= 55) return { ring: "stroke-accent", text: "text-accent" };
  return { ring: "stroke-text-subtle", text: "text-text-muted" };
}

export function ScoreRing({ score, size = 44 }: { score: number; size?: number }) {
  const tone = scoreTone(score);
  const radius = (size - 6) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = (Math.max(0, Math.min(100, score)) / 100) * circumference;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth="3"
          className="stroke-border"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          className={tone.ring}
        />
      </svg>
      <span
        className={`absolute inset-0 flex items-center justify-center text-sm font-semibold ${tone.text}`}
      >
        {score}
      </span>
    </div>
  );
}

function FactorBar({ factor }: { factor: ScoreFactor }) {
  // Width is relative to the factor's own maximum contribution, so a bar being "full" means
  // "this factor is as good as it can be" rather than "this factor dominates the score".
  const maxPoints = factor.weight * 100;
  const ratio = maxPoints > 0 ? Math.max(0, Math.min(1, factor.points / maxPoints)) : 0;

  return (
    <li className="grid grid-cols-[7rem_1fr_auto] items-center gap-3 text-xs">
      <span className="text-text-muted">{factor.label_fr}</span>
      <span className="h-1.5 overflow-hidden rounded-full bg-border" role="presentation">
        <span
          className={`block h-full rounded-full ${ratio >= 0.8 ? "bg-success" : ratio > 0 ? "bg-accent" : "bg-transparent"}`}
          style={{ width: `${ratio * 100}%` }}
        />
      </span>
      <span className="tabular-nums text-text-subtle">
        {factor.points.toFixed(1)} / {maxPoints.toFixed(0)}
      </span>
      <span className="col-span-3 -mt-1 text-[11px] text-text-subtle">{factor.detail}</span>
    </li>
  );
}

export function ScoreBreakdownList({
  breakdown,
  title,
}: {
  breakdown: ScoreBreakdown;
  title: string;
}) {
  const total = breakdown.factors.reduce((sum, factor) => sum + factor.points, 0);

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wide text-text-subtle">{title}</p>
      <ul className="space-y-2">
        {breakdown.factors.map((factor) => (
          <FactorBar key={factor.key} factor={factor} />
        ))}
      </ul>
      <p className="border-t border-border pt-2 text-xs text-text-muted">
        {/* Showing the sum next to the score is the check §8.4 asks the UI to make visible:
            if these ever disagree, the explanation is wrong and the user can see it. */}
        <span className="tabular-nums">{total.toFixed(1)}</span> = {breakdown.score}
      </p>
      {breakdown.warnings.length > 0 && (
        <ul className="space-y-1 pt-1">
          {breakdown.warnings.map((warning) => (
            <li key={warning} className="bp-chip bg-warning-subtle text-warning">
              {warning}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
