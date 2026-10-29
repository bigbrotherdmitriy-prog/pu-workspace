import { expect, it } from "vitest";
import source from "./App.tsx?raw";

// A screen rendered after </main> without the module-overlay wrapper is invisible (owner 29.10: five blank screens).
it("renders every standalone module screen inside the module-overlay wrapper", () => {
  const modules = ["ExecutiveDocsModule", "RdModule", "PurchaseModule", "EquipmentModule", "WorkLogModule", "PuStorageModule", "EstimatesModule"];
  const lines = source.split("\n");
  for (const name of modules) {
    const line = lines.find((l: string) => l.includes(`<${name} `) && l.includes("active ==="));
    expect(line, `${name} is rendered from App.tsx`).toBeTruthy();
    expect(line, `${name} is wrapped in module-overlay`).toContain("module-overlay");
  }
});
