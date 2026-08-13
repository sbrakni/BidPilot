"use client";

/**
 * The three onboarding steps (SPEC §15.3).
 *
 * Two behaviours here are product decisions rather than UI detail:
 *
 *   * **A registry outage must not dead-end onboarding.** If the lookup fails, the wizard offers
 *     manual entry instead of an error page. Losing a signup because data.gouv.fr is slow would be
 *     an expensive way to be strict.
 *   * **The registry pre-fills; the user confirms** (P6). Nothing derived from the lookup becomes
 *     the profile until step 3 is submitted - including the CPV families suggested from the NAF
 *     code, which decide what the user will and will not see.
 */

import { useState, useTransition } from "react";

import { bootstrapProfile, saveProfile } from "@/app/actions";
import { useRouter } from "@/i18n/routing";

/** Sector packs are seed data (SPEC §4.2); these are the launch three, by label and CPV set. */
const SECTOR_PACKS = [
  { key: "it", labelFr: "IT & numérique", labelEn: "IT & digital", cpv: ["72", "48", "302", "79511"] },
  { key: "training", labelFr: "Formation", labelEn: "Training", cpv: ["805", "79632", "80533"] },
  {
    key: "maintenance",
    labelFr: "Maintenance & facilities",
    labelEn: "Maintenance & facilities",
    cpv: ["50", "45259", "90910", "71314"],
  },
] as const;

const FRENCH_REGIONS = [
  { nuts: "FR10", label: "Île-de-France" },
  { nuts: "FRB0", label: "Centre-Val de Loire" },
  { nuts: "FRC1", label: "Bourgogne" },
  { nuts: "FRC2", label: "Franche-Comté" },
  { nuts: "FRD1", label: "Basse-Normandie" },
  { nuts: "FRD2", label: "Haute-Normandie" },
  { nuts: "FRE1", label: "Nord-Pas de Calais" },
  { nuts: "FRE2", label: "Picardie" },
  { nuts: "FRF1", label: "Alsace" },
  { nuts: "FRF2", label: "Champagne-Ardenne" },
  { nuts: "FRF3", label: "Lorraine" },
  { nuts: "FRG0", label: "Pays de la Loire" },
  { nuts: "FRH0", label: "Bretagne" },
  { nuts: "FRI1", label: "Aquitaine" },
  { nuts: "FRI2", label: "Limousin" },
  { nuts: "FRI3", label: "Poitou-Charentes" },
  { nuts: "FRJ1", label: "Languedoc-Roussillon" },
  { nuts: "FRJ2", label: "Midi-Pyrénées" },
  { nuts: "FRK1", label: "Auvergne" },
  { nuts: "FRK2", label: "Rhône-Alpes" },
  { nuts: "FRL0", label: "Provence-Alpes-Côte d'Azur" },
  { nuts: "FRM0", label: "Corse" },
];

type Identity = {
  siren: string;
  legalName: string;
  naf: string | null;
  effectifBand: string | null;
  headcountRange: { min: number; max: number | null } | null;
  address: { city: string | null; postcode: string | null; nuts: string | null };
};

/** Explicit keys rather than Record<string, string>, so a missing translation is a type error. */
type Labels = {
  /**
   * Pre-rendered per step, one string per step.
   *
   * A `(current, total) => string` formatter cannot cross the server/client boundary - functions
   * are not serializable in React Server Components - and passing one fails at render time rather
   * than at compile time, so it is worth stating why the shape is what it is.
   */
  steps: string[];
  siret: {
    title: string;
    body: string;
    placeholder: string;
    submit: string;
    manual: string;
    notFound: string;
    unavailable: string;
    invalid: string;
  };
  confirm: {
    title: string;
    body: string;
    legalName: string;
    naf: string;
    headcountBand: string;
    location: string;
    unknown: string;
    submit: string;
  };
  scope: {
    title: string;
    body: string;
    sectorPack: string;
    zones: string;
    national: string;
    revenue: string;
    revenueHint: string;
    submit: string;
  };
};

const TOTAL_STEPS = 3;

export function OnboardingWizard({ labels }: { labels: Labels }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [step, setStep] = useState(1);
  const [identifier, setIdentifier] = useState("");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [pack, setPack] = useState<string>("it");
  const [national, setNational] = useState(false);
  const [zones, setZones] = useState<string[]>([]);
  const [revenue, setRevenue] = useState("");

  function lookup() {
    setError(null);
    startTransition(async () => {
      const result = await bootstrapProfile(identifier);
      if (result.ok && result.identity) {
        setIdentity(result.identity as Identity);
        // Pre-select the region the registry gave, since it is right far more often than not.
        if ((result.identity as Identity).address.nuts) {
          setZones([(result.identity as Identity).address.nuts as string]);
        }
        setStep(2);
      } else {
        const reason = result.reason ?? "unavailable";
        setError(
          reason === "invalid"
            ? labels.siret.invalid
            : reason === "notFound"
              ? labels.siret.notFound
              : labels.siret.unavailable,
        );
      }
    });
  }

  function skipToManual() {
    setIdentity(null);
    setStep(3);
  }

  function finish() {
    setError(null);
    const selected = SECTOR_PACKS.find((p) => p.key === pack) ?? SECTOR_PACKS[0];
    const amount = Number(revenue.replace(/[^\d]/g, ""));
    startTransition(async () => {
      const result = await saveProfile({
        cpvFamilies: [...selected.cpv],
        zones: { nuts: national ? [] : zones, national, max_distance_km: null },
        // Revenue is optional: it only powers the size-fit warning, and demanding it here would
        // add friction to the five-minute path for a factor that degrades gracefully.
        revenues: Number.isFinite(amount) && amount > 0 ? [{ year: new Date().getFullYear(), amount }] : [],
      });
      if (result.ok) {
        router.push("/opportunities");
      } else {
        setError(result.error ?? labels.siret.unavailable);
      }
    });
  }

  const buttonPrimary =
    "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50";

  return (
    <div className="max-w-2xl">
      <p className="mb-4 text-xs text-text-subtle">{labels.steps[step - 1]}</p>

      {step === 1 && (
        <section className="bp-card p-5">
          <h2 className="text-sm font-medium">{labels.siret.title}</h2>
          <p className="mt-1 text-sm text-text-muted">{labels.siret.body}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <input
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && identifier.trim()) lookup();
              }}
              placeholder={labels.siret.placeholder}
              inputMode="numeric"
              aria-label={labels.siret.title}
              className="min-w-56 flex-1 rounded-md border border-border bg-surface px-3 py-1.5 text-sm"
            />
            <button type="button" onClick={lookup} disabled={pending || !identifier.trim()} className={buttonPrimary}>
              {labels.siret.submit}
            </button>
          </div>
          {error && (
            <div className="mt-3">
              <p className="text-xs text-warning">{error}</p>
              {/* The escape hatch: a registry outage must not cost a signup. */}
              <button type="button" onClick={skipToManual} className="mt-1 text-xs text-accent hover:underline">
                {labels.siret.manual}
              </button>
            </div>
          )}
        </section>
      )}

      {step === 2 && identity && (
        <section className="bp-card p-5">
          <h2 className="text-sm font-medium">{labels.confirm.title}</h2>
          <p className="mt-1 text-sm text-text-muted">{labels.confirm.body}</p>
          <dl className="mt-4 grid gap-3 sm:grid-cols-2">
            <Field label={labels.confirm.legalName} value={identity.legalName} />
            <Field label="SIREN" value={identity.siren} />
            <Field label={labels.confirm.naf} value={identity.naf ?? labels.confirm.unknown} />
            <Field
              label={labels.confirm.headcountBand}
              value={
                identity.headcountRange
                  ? `${identity.headcountRange.min}${identity.headcountRange.max ? `–${identity.headcountRange.max}` : "+"}`
                  : labels.confirm.unknown
              }
            />
            <Field
              label={labels.confirm.location}
              value={
                [identity.address.postcode, identity.address.city].filter(Boolean).join(" ") ||
                labels.confirm.unknown
              }
            />
          </dl>
          <button type="button" onClick={() => setStep(3)} className={`mt-4 ${buttonPrimary}`}>
            {labels.confirm.submit}
          </button>
        </section>
      )}

      {step === 3 && (
        <section className="bp-card p-5">
          <h2 className="text-sm font-medium">{labels.scope.title}</h2>
          <p className="mt-1 text-sm text-text-muted">{labels.scope.body}</p>

          <fieldset className="mt-4">
            <legend className="text-xs font-medium text-text-muted">{labels.scope.sectorPack}</legend>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {SECTOR_PACKS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setPack(option.key)}
                  aria-pressed={pack === option.key}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                    pack === option.key
                      ? "bg-accent-subtle text-accent"
                      : "border border-border text-text-muted hover:bg-surface"
                  }`}
                >
                  {option.labelFr}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="mt-4">
            <legend className="text-xs font-medium text-text-muted">{labels.scope.zones}</legend>
            <label className="mt-2 flex items-center gap-2 text-xs">
              <input type="checkbox" checked={national} onChange={(e) => setNational(e.target.checked)} />
              {labels.scope.national}
            </label>
            {!national && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {FRENCH_REGIONS.map((region) => {
                  const selected = zones.includes(region.nuts);
                  return (
                    <button
                      key={region.nuts}
                      type="button"
                      aria-pressed={selected}
                      onClick={() =>
                        setZones(
                          selected ? zones.filter((z) => z !== region.nuts) : [...zones, region.nuts],
                        )
                      }
                      className={`rounded-md px-2 py-0.5 text-xs ${
                        selected
                          ? "bg-accent-subtle text-accent"
                          : "border border-border text-text-muted hover:bg-surface"
                      }`}
                    >
                      {region.label}
                    </button>
                  );
                })}
              </div>
            )}
          </fieldset>

          <div className="mt-4">
            <label className="text-xs font-medium text-text-muted" htmlFor="revenue">
              {labels.scope.revenue}
            </label>
            <input
              id="revenue"
              value={revenue}
              onChange={(event) => setRevenue(event.target.value)}
              inputMode="numeric"
              className="mt-1 block w-48 rounded-md border border-border bg-surface px-3 py-1.5 text-sm"
            />
            <p className="mt-1 text-[11px] text-text-subtle">{labels.scope.revenueHint}</p>
          </div>

          <button
            type="button"
            onClick={finish}
            disabled={pending || (!national && zones.length === 0)}
            className={`mt-5 ${buttonPrimary}`}
          >
            {labels.scope.submit}
          </button>
          {error && <p className="mt-2 text-xs text-danger">{error}</p>}
        </section>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-text-subtle">{label}</dt>
      <dd className="text-sm">{value}</dd>
    </div>
  );
}
