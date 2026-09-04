import { describe, expect, it } from "vitest";
import { buildContractTree } from "./contractTree";

describe("buildContractTree", () => {
  const contracts = [
    { id: 4, number: "СС-2", title: "Субсубподряд", contract_kind: "downstream_subcontract", parent_contract_id: 3 },
    { id: 1, number: "ГП-1", title: "Генподряд", contract_kind: "prime_reference" },
    { id: 3, number: "СП-1", title: "Субподряд", contract_kind: "downstream_subcontract", parent_contract_id: 2 },
    { id: 2, number: "Д-1", title: "Наш договор", contract_kind: "revenue_subcontract", parent_contract_id: 1 },
  ];

  it("orders an unlimited subcontract chain from the prime contract", () => {
    expect(buildContractTree(contracts).map(({ item, depth }) => [item.id, depth])).toEqual([
      [1, 0], [2, 1], [3, 2], [4, 3],
    ]);
  });

  it("keeps ancestors visible when searching for a deep contractor", () => {
    expect(buildContractTree(contracts, "Субсубподряд").map(({ item }) => item.id)).toEqual([1, 2, 3, 4]);
  });
});
