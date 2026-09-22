import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GprContractContext } from "./GprContractContext";

afterEach(cleanup);

describe("GprContractContext", () => {
  it("selects only a financial contract for the separate schedule workspace", () => {
    const onSelectContract = vi.fn();
    render(<GprContractContext
      contracts={[
        { id: 1, number: "ГП-1", title: "Контекст", contract_kind: "prime_reference" },
        { id: 2, number: "Д-2", title: "Монтаж", contract_kind: "supply" },
      ]}
      selectedContractId={0}
      onSelectContract={onSelectContract}
    />);

    expect(screen.queryByRole("option", { name: /ГП-1/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Договор для графика работ"), { target: { value: "2" } });
    expect(onSelectContract).toHaveBeenCalledWith(2);
  });
});
