import { describe, expect, it } from "vitest";
import mainSource from "../main.tsx?raw";

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

});
