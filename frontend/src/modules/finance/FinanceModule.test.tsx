import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FinanceModule } from "./FinanceModule";

afterEach(cleanup);

describe("finance document ingress", () => {
  it("offers a direct invoice or act upload entrypoint", () => {
    const onUpload = vi.fn();
    render(<FinanceModule
      finance={null}
      candidates={[]}
      contracts={[]}
      selectedContractId={0}
      onSelectContract={vi.fn()}
      onPrepare={vi.fn()}
      onUseCandidate={vi.fn()}
      onUpload={onUpload}
      onReload={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Загрузить счёт или акт" }));
    expect(onUpload).toHaveBeenCalledOnce();
  });
});
