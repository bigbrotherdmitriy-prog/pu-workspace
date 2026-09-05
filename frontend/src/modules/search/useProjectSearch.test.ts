import { describe, expect, it } from "vitest";
import { searchPath, toSearchHits } from "./useProjectSearch";


describe("project search read model", () => {
  it("encodes the query and bounds the requested result count", () => {
    const url = new URL(searchPath(17, " Д-42 & фасад "), "https://local.test");
    expect(url.pathname).toBe("/api/search/projects/17");
    expect(url.searchParams.get("query")).toBe("Д-42 & фасад");
    expect(url.searchParams.get("limit")).toBe("30");
  });

  it("maps only structurally safe result rows", () => {
    const hits = toSearchHits({
      items: [{
        entity_type: "contract",
        entity_id: 42,
        name: "Д-42 — Монтаж фасада",
        date: "2026-08-15",
        project: { id: 17, name: "Север" },
        contract: { id: 42, number: "Д-42", title: "Монтаж фасада" },
        counterparty: "ООО Синтетика",
        status: "active",
        links: [{ relation: "entity", href: "/projects/17/contracts/42" }],
      }],
      next_cursor: null,
      limit: 30,
      scan_truncated: false,
      scan_cap_per_type: 1000,
      external_actions_created: false,
    });
    expect(hits).toEqual([{
      id: 42,
      kind: "contract",
      title: "Д-42 — Монтаж фасада",
      detail: "Д-42 · ООО Синтетика · active · 2026-08-15",
    }]);
  });

  it("fails closed for malformed or effect-bearing responses", () => {
    expect(toSearchHits({ external_actions_created: true } as never)).toEqual([]);
    expect(toSearchHits({ items: [{ entity_id: "42" }] } as never)).toEqual([]);
    expect(toSearchHits({
      items: [{ entity_type: "raw_sql", entity_id: 1, name: "bad", links: [] }],
      external_actions_created: false,
    } as never)).toEqual([]);
  });
});
