import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import mainSource from "../main.tsx?raw";

const themeSource = readFileSync(
  resolve(process.cwd(), "src/interface-v6-workspace.css"),
  "utf8",
);

describe("workspace visual theme", () => {
  it("loads the calm workspace theme instead of the retired neon theme", () => {
    expect(mainSource).toContain('import "./interface-v6-workspace.css"');
    expect(mainSource).not.toContain('import "./interface-v5-neon.css"');
  });

  it("loads the workspace theme after the structural interface styles", () => {
    expect(mainSource.indexOf('import "./interface-v6-workspace.css"')).toBeGreaterThan(
      mainSource.indexOf('import "./interface-v4.css"'),
    );
  });

  it("keeps the primary navigation above fixed module workspaces", () => {
    expect(themeSource).toMatch(/\.shell\s*>\s*aside\s*\{[^}]*z-index:\s*20;/s);
  });
});
