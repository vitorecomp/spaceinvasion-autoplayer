import { afterEach, describe, expect, it } from "vitest";
import {
  bottleneckTag,
  clampPct,
  finiteNum,
  fmtCompact,
  fmtImprovementPct,
  fmtInt,
  fmtRate,
  getSelectedBlastFurnacePct,
  renderResourceBarDom,
} from "./formatters";

describe("formatters numeric safety, negative-zero, and fallback guards", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("finiteNum rejects objects, arrays, blank strings, symbols, booleans, NaN, and Infinity", () => {
    expect(finiteNum({})).toBeNull();
    expect(finiteNum([])).toBeNull();
    expect(finiteNum("", -1)).toBe(-1);
    expect(finiteNum("   ", -1)).toBe(-1);
    expect(finiteNum(Symbol("x"), -1)).toBe(-1);
    expect(finiteNum(true, -1)).toBe(-1);
    expect(finiteNum(Number.NaN, 0)).toBe(0);
    expect(finiteNum(Number.POSITIVE_INFINITY, 0)).toBe(0);
    expect(finiteNum(Number.NEGATIVE_INFINITY, 0)).toBe(0);
    expect(finiteNum("42.5", 0)).toBe(42.5);
    expect(finiteNum(42.5, 0)).toBe(42.5);
  });

  it("fmtInt, fmtRate, and fmtCompact suppress -0 and sub-threshold negative zero cleanly", () => {
    expect(fmtInt(-0)).toBe("0");
    expect(fmtInt(0)).toBe("0");
    expect(fmtInt(Number.NaN)).toBe("—");
    expect(fmtInt(Number.POSITIVE_INFINITY)).toBe("—");

    expect(fmtRate(-0)).toBe("0/h");
    expect(fmtRate(-0.1)).toBe("0/h");
    expect(fmtRate(-0.4)).toBe("0/h");
    expect(fmtRate(-1)).toBe("-1/h");
    expect(fmtRate(25)).toBe("+25/h");
    expect(fmtRate(Number.NaN)).toBe("—/h");

    expect(fmtCompact(-0.01)).toBe("0.0");
    expect(fmtCompact(-0.1)).toBe("-0.1");
    expect(fmtCompact(Number.POSITIVE_INFINITY)).toBe("—");
    expect(fmtCompact(Number.NaN)).toBe("—");
  });

  it("fmtImprovementPct handles undefined, clamping (>+999.9% / <-999.9%), and tiny negative zero", () => {
    expect(fmtImprovementPct(undefined)).toBe("—");
    expect(fmtImprovementPct(null, null, null)).toBe("—");
    expect(fmtImprovementPct(-0.01)).toBe("+0.0%");
    expect(fmtImprovementPct(0.01)).toBe("+0.0%");
    expect(fmtImprovementPct(1500)).toBe(">+999.9%");
    expect(fmtImprovementPct(-1500)).toBe("<-999.9%");
    expect(fmtImprovementPct(null, 0, 10)).toBe("+999.9%");
    expect(fmtImprovementPct(null, 100, 125)).toBe("+25.0%");
  });

  it("bottleneckTag trims whitespace and maps resource keys cleanly", () => {
    expect(bottleneckTag(null)).toBe("");
    expect(bottleneckTag(undefined)).toBe("");
    expect(bottleneckTag("   ")).toBe("");
    expect(bottleneckTag("pig_iron")).toBe("PIG");
    expect(bottleneckTag("metal")).toBe("MET");
    expect(bottleneckTag("kryptonite")).toBe("KRY");
    expect(bottleneckTag("spice")).toBe("SPI");
  });

  it("getSelectedBlastFurnacePct clamps values and falls back cleanly on corrupt economy_rates", () => {
    expect(clampPct("bad")).toBe(0);
    expect(clampPct(-50)).toBe(0);
    expect(clampPct(250)).toBe(100);

    const corruptDash: any = {
      state: {
        active_planet_id: 1,
        planets: [{ planet_id: 1, economy_rates: { resource2: "bad" } }],
      },
      buildings_eta: {
        planet: { planet_id: 1 },
        planet_economy: { blast_furnace_pct: 0 },
      },
    };
    expect(getSelectedBlastFurnacePct(corruptDash, 1, null)).toBe(0);

    const allCorruptDash: any = {
      state: {
        active_planet_id: 1,
        planets: [{ planet_id: 1, economy_rates: { resource2: "NaN" } }],
      },
      buildings_eta: {
        planet: { planet_id: 1 },
        planet_economy: { blast_furnace_pct: "corrupt" },
      },
    };
    expect(getSelectedBlastFurnacePct(allCorruptDash, 1, null)).toBe(100);
  });

  it("renderResourceBarDom avoids negative styling for -0.1/h and applies is-neg for -1/h without NaN/Infinity", () => {
    document.body.innerHTML = `
      <span id="val-pig"></span>
      <span id="cap-pig"></span>
      <div id="bar-pig"></div>
      <span id="rate-pig"></span>
    `;

    renderResourceBarDom("pig", 5000, 100000, -0.1);
    expect(document.getElementById("rate-pig")!.textContent).toBe("0/h");
    expect(document.getElementById("rate-pig")!.className).not.toContain("is-neg");

    renderResourceBarDom("pig", 5000, 100000, -1);
    expect(document.getElementById("rate-pig")!.textContent).toBe("-1/h");
    expect(document.getElementById("rate-pig")!.className).toContain("is-neg");

    renderResourceBarDom("pig", Number.POSITIVE_INFINITY, Number.NaN, Number.NaN);
    expect(document.getElementById("val-pig")!.textContent).not.toContain("Infinity");
    expect(document.getElementById("cap-pig")!.textContent).not.toContain("NaN");
    expect(document.getElementById("rate-pig")!.textContent).not.toContain("NaN");
  });
});
