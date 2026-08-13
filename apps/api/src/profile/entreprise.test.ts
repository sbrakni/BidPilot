/**
 * SIRET bootstrap mapping tests (SPEC §7.1).
 *
 * Run against the captured fixture, not the live registry: a suite that depends on data.gouv.fr
 * being up is a flaky suite, and the fixture is what pins the field shapes that actually bit us -
 * dotted NAF codes, an employee *band* where a headcount looks like it should be, and a commune
 * *code* where a city name looks like it should be.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  EFFECTIF_BANDS,
  EntrepriseLookupError,
  deptToNuts,
  lookupCompany,
  mapIdentity,
  normalizeNaf,
  parseIdentifier,
} from "./entreprise.js";

const FIXTURE = path.resolve(import.meta.dirname, "../../../../fixtures/entreprises/ipsos.json");
const fixture = JSON.parse(readFileSync(FIXTURE, "utf8")) as {
  results: Array<Record<string, unknown>>;
};

function fixtureFetch(status = 200, body: unknown = fixture): typeof fetch {
  return (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
}

describe("identifier parsing", () => {
  it("accepts a SIREN and a SIRET, and derives the SIREN from the SIRET", () => {
    expect(parseIdentifier("304555634")).toEqual({ siren: "304555634", siret: null });
    expect(parseIdentifier("30455563400024")).toEqual({
      siren: "304555634",
      siret: "30455563400024",
    });
  });

  it("tolerates the spacing people actually type", () => {
    expect(parseIdentifier("304 555 634").siren).toBe("304555634");
  });

  it("rejects anything that is not 9 or 14 digits", () => {
    for (const bad of ["", "123", "1234567890"]) {
      expect(() => parseIdentifier(bad)).toThrow(EntrepriseLookupError);
    }
  });
});

describe("field normalisation", () => {
  it("strips the dot INSEE puts in NAF codes", () => {
    // Our naf_cpv_map is keyed without it, so "70.10Z" would silently match nothing.
    expect(normalizeNaf("70.10Z")).toBe("7010Z");
    expect(normalizeNaf("62.02A")).toBe("6202A");
    expect(normalizeNaf(null)).toBeNull();
  });

  it("maps départements to NUTS, matching the ingestion service", () => {
    expect(deptToNuts("75")).toBe("FR10");
    expect(deptToNuts("2A")).toBe("FRM0");
    expect(deptToNuts("974")).toBe("FRY4");
    expect(deptToNuts("6")).toBe("FRL0");
    expect(deptToNuts("99")).toBeNull();
    expect(deptToNuts(null)).toBeNull();
  });

  it("covers every INSEE employee band with a coherent range", () => {
    for (const [band, range] of Object.entries(EFFECTIF_BANDS)) {
      expect(range.min, band).toBeGreaterThanOrEqual(0);
      if (range.max !== null) expect(range.max, band).toBeGreaterThanOrEqual(range.min);
    }
  });
});

describe("identity mapping", () => {
  it("maps the real payload the registry returns", () => {
    const identity = mapIdentity(fixture.results[0] as never, null);
    expect(identity.siren).toBe("304555634");
    expect(identity.legalName).toBe("IPSOS");
    expect(identity.naf).toBe("7010Z");
    // The human name, not the INSEE commune code - "75113" on a confirmation screen is useless.
    expect(identity.address.city).toBe("PARIS");
    expect(identity.address.nuts).toBe("FR10");
    expect(identity.executives.length).toBeGreaterThan(0);
  });

  it("keeps the employee band and derives a range, never a headcount", () => {
    // The registry gives a band; inventing a precise headcount from it would breach P1 and feed a
    // wrong number into the size-fit factor of every match score.
    const identity = mapIdentity(fixture.results[0] as never, null);
    expect(identity.effectifBand).toBe("01");
    expect(identity.headcountRange).toEqual({ min: 1, max: 2 });
    expect(identity as unknown as Record<string, unknown>).not.toHaveProperty("headcount");
  });

  it("leaves the range null when the band is absent rather than guessing zero", () => {
    const identity = mapIdentity({ siren: "123456789", nom_complet: "X" } as never, null);
    expect(identity.effectifBand).toBeNull();
    expect(identity.headcountRange).toBeNull();
  });

  it("prefers the requested establishment over the head office", () => {
    const identity = mapIdentity(
      {
        siren: "123456789",
        nom_complet: "Multi-site",
        siege: { siret: "12345678900011", departement: "75" },
        matching_etablissements: [{ siret: "12345678900029", departement: "35" }],
      } as never,
      "12345678900029",
    );
    expect(identity.siret).toBe("12345678900029");
    expect(identity.address.nuts).toBe("FRH0");
  });
});

describe("city labelling", () => {
  it("never presents an INSEE commune code as a city name", () => {
    // Establishment records sometimes carry only the code. "75113" on a confirmation screen looks
    // like a mistake to the user, and the postcode beside it already locates them.
    const identity = mapIdentity(
      {
        siren: "123456789",
        nom_complet: "Sans libellé",
        siege: { siret: "12345678900011", commune: "75113", code_postal: "75013", departement: "75" },
      } as never,
      null,
    );
    expect(identity.address.city).toBeNull();
    expect(identity.address.postcode).toBe("75013");
  });

  it("keeps a real city name when the registry provides one", () => {
    const identity = mapIdentity(
      {
        siren: "123456789",
        nom_complet: "Avec libellé",
        siege: { commune: "35238", libelle_commune: "RENNES", departement: "35" },
      } as never,
      null,
    );
    expect(identity.address.city).toBe("RENNES");
  });
});

describe("lookup failure modes", () => {
  it("reports not_found when the registry has no such company", async () => {
    await expect(
      lookupCompany("999999999", fixtureFetch(200, { results: [] })),
    ).rejects.toMatchObject({ reason: "not_found" });
  });

  it("reports unavailable on a registry error, so onboarding can fall back to manual entry", async () => {
    await expect(lookupCompany("304555634", fixtureFetch(503, {}))).rejects.toMatchObject({
      reason: "unavailable",
    });
  });

  it("reports unavailable rather than hanging when the registry does not answer", async () => {
    const hanging = (async () => {
      throw new Error("network timeout");
    }) as unknown as typeof fetch;
    await expect(lookupCompany("304555634", hanging)).rejects.toMatchObject({
      reason: "unavailable",
    });
  });

  it("returns the company on the happy path", async () => {
    const identity = await lookupCompany("304555634", fixtureFetch());
    expect(identity.legalName).toBe("IPSOS");
  });
});
