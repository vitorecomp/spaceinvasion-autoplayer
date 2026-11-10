import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getStrategySettingSpec } from "./configSchema";
import { SchemaSettingField } from "./SchemaSettingField";

afterEach(() => {
  cleanup();
});

describe("SchemaSettingField (Phase 5.4 unified schema-driven settings)", () => {
  it("renders .config-item with #cfg-<key> from configSchema single source of truth", () => {
    const onChange = vi.fn();
    const spec = getStrategySettingSpec("storage_warn_ratio");
    const { container } = render(
      <SchemaSettingField
        variant="config"
        idPrefix="cfg"
        settingKey="storage_warn_ratio"
        value={0.85}
        emitOnInput
        onChange={onChange}
      />
    );

    const row = container.querySelector(".config-item");
    expect(row).not.toBeNull();
    const input = container.querySelector("#cfg-storage_warn_ratio") as HTMLInputElement | null;
    expect(input).not.toBeNull();
    expect(input?.type).toBe("number");
    expect(input?.min).toBe(String(spec.min));
    expect(input?.max).toBe(String(spec.max));
    expect(input?.step).toBe(String(spec.step));
    expect(container.textContent).toContain(spec.title);
    expect(container.textContent).toContain(spec.boundsLabel);

    fireEvent.change(input!, { target: { value: "0.9" } });
    expect(onChange).toHaveBeenCalledWith("0.9");
  });

  it("renders .ag-param-item with prefix-parameterized IDs (#ag-cfg-*, #flt-cfg-*, #co-param-*)", () => {
    const onAgChange = vi.fn();
    const onFltChange = vi.fn();
    const { container } = render(
      <div>
        <SchemaSettingField
          variant="panel"
          idPrefix="ag-cfg"
          settingKey="combat_safety_margin"
          value="1.4"
          onChange={onAgChange}
        />
        <SchemaSettingField
          variant="panel"
          idPrefix="flt-cfg"
          settingKey="allow_raids"
          value={false}
          onChange={onFltChange}
        />
        <SchemaSettingField
          variant="panel"
          idPrefix="flt-cfg"
          settingKey="ai_resource_spare_pct"
          value="10"
          onChange={onFltChange}
        />
      </div>
    );

    expect(container.querySelectorAll(".ag-param-item").length).toBe(3);

    const agMargin = container.querySelector("#ag-cfg-combat_safety_margin") as HTMLInputElement;
    expect(agMargin).not.toBeNull();
    expect(agMargin.min).toBe("1");
    expect(agMargin.max).toBe("1000");
    fireEvent.change(agMargin, { target: { value: "1.6" } });
    expect(onAgChange).toHaveBeenCalledWith("1.6");

    const fltRaids = container.querySelector("#flt-cfg-allow_raids") as HTMLInputElement;
    expect(fltRaids).not.toBeNull();
    expect(fltRaids.type).toBe("checkbox");
    expect(fltRaids.className).toBe("config-checkbox");
    fireEvent.click(fltRaids);
    expect(onFltChange).toHaveBeenCalledWith(true);

    const fltSpare = container.querySelector("#flt-cfg-ai_resource_spare_pct") as HTMLInputElement;
    expect(fltSpare).not.toBeNull();
    expect(fltSpare.max).toBe("90");
  });

  it("supports planet-select in config variant and falls back to numeric input in panel variant", () => {
    const onChange = vi.fn();
    const { container } = render(
      <div>
        <SchemaSettingField
          variant="config"
          idPrefix="cfg"
          settingKey="spy_dispatch_planet_id"
          value="42"
          planetSelectContext={{
            nonRaidPlanets: [
              { planet_id: 42, name: "Homeworld", coords: "1:200:8", ships: { s10: 25 } } as any,
            ],
            savedSpyPid: 42,
            hasSavedPlanetOption: true,
          }}
          onChange={onChange}
        />
        <SchemaSettingField
          variant="panel"
          idPrefix="flt-cfg"
          settingKey="spy_dispatch_planet_id"
          value="42"
          onChange={onChange}
        />
      </div>
    );

    const cfgSelect = container.querySelector("#cfg-spy_dispatch_planet_id");
    expect(cfgSelect?.tagName).toBe("SELECT");
    expect(cfgSelect?.textContent).toContain("Homeworld [1:200:8]");

    const fltNum = container.querySelector("#flt-cfg-spy_dispatch_planet_id") as HTMLInputElement;
    expect(fltNum?.tagName).toBe("INPUT");
    expect(fltNum?.type).toBe("number");
  });
});
