import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContractScheme } from "./ContractScheme";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

function contractRow(container: HTMLElement, number: string) {
  return within(container).getByText(number, { selector: ".contract-register-open" }).closest("tr")!;
}

function expandContract(container: HTMLElement, number: string) {
  fireEvent.click(within(container).getByText(number, { selector: ".contract-register-open" }));
  return container.querySelector<HTMLElement>(".contract-register-detail")!;
}

function expandActions(container: HTMLElement, number: string) {
  const panel = expandContract(container, number);
  fireEvent.click(within(panel).getByText("Действия с договором", { selector: "summary" }));
  return panel;
}

function fileTransfer(files: File[]) {
  return { files, types: ["Files"], getData: () => "" };
}

describe("ContractScheme", () => {
  it("opens the shared details from register and scheme and uses execution end for the deadline", () => {
    const { container } = render(<ContractScheme projectId={6} contracts={[
      { id: 1, number: "SYN-1", title: "Synthetic", performed_to: "2026-12-31", warranty_until: "2027-12-31" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} renderDetails={(contract) => <p>Shared details {contract.id}</p>} />);
    expect(contractRow(container, "SYN-1").querySelector(".contract-register-deadline")).toHaveTextContent("31.12.2026");
    expandContract(container, "SYN-1");
    expect(within(container).getByText("Shared details 1")).toBeInTheDocument();
    fireEvent.click(within(container).getByRole("button", { name: "Схема связей" }));
    expect(within(container).getByText("Shared details 1")).toBeInTheDocument();
  });

  it("opens a six-column register without selecting a contract and filters only matching rows", () => {
    const { container } = render(<ContractScheme projectId={6} contracts={[
      { id: 1, number: "ГП-1", title: "Генподряд", counterparty: "Заказчик", contract_kind: "prime_reference" },
      { id: 2, number: "СП-2", title: "Монтаж", counterparty: "Исполнитель", contract_kind: "downstream_subcontract", parent_contract_id: 1 },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} />);

    const table = within(container).getByRole("table", { name: "Реестр договоров" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Договор", "Контрагент", "Сумма", "Срок окончания", "Статус", "Файлы",
    ]);
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(container.querySelectorAll('.contract-register-open[aria-expanded="false"]')).toHaveLength(2);
    expect(container.querySelector(".contract-register-detail")).not.toBeInTheDocument();
    expect(container.querySelector(".contract-register-expanded")).not.toBeInTheDocument();
    expect(container.querySelector(".contract-scheme-scroll")).not.toBeInTheDocument();
    expect(container.querySelector(".contract-scheme-file-drop")).not.toBeInTheDocument();
    fireEvent.change(within(container).getByLabelText("Поиск договоров"), { target: { value: "  ИСПОЛНИТЕЛЬ  " } });
    expect(contractRow(container, "СП-2")).toBeInTheDocument();
    expect(within(container).queryByText("ГП-1", { selector: ".contract-register-open" })).not.toBeInTheDocument();
    expect(container.querySelectorAll(".contract-register-row")).toHaveLength(1);
    expect(container.querySelector(".contract-register-count")).toHaveTextContent("Найдено: 1");
    fireEvent.change(within(container).getByLabelText("Фильтр типа договора"), { target: { value: "prime_reference" } });
    expect(within(container).getByText("Ничего не найдено")).toBeInTheDocument();
    expect(container.querySelector(".contract-register-count")).toHaveTextContent("Найдено: 0");
  });

  it("preserves large decimal amounts and distinguishes zero from an absent amount", () => {
    const { container } = render(<ContractScheme projectId={20} contracts={[
      { id: 1, number: "Д-1", title: "Большая сумма", amount: "9999999999999999.99", status: "draft" },
      { id: 2, number: "Д-2", title: "Нулевая сумма", amount: 0 },
      { id: 3, number: "Д-3", title: "Без суммы" },
      { id: 4, number: "Д-4", title: "Пустая сумма", amount: null },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} />);
    expect(contractRow(container, "Д-1").querySelector(".contract-register-amount")?.textContent).toBe("9\u00a0999\u00a0999\u00a0999\u00a0999\u00a0999,99 ₽");
    expect(contractRow(container, "Д-2").querySelector(".contract-register-amount")).toHaveTextContent("0,00 ₽");
    expect(contractRow(container, "Д-3").querySelector(".contract-register-amount")).toHaveTextContent("Не указана");
    expect(contractRow(container, "Д-4").querySelector(".contract-register-amount")).toHaveTextContent("Не указана");
    expect(contractRow(container, "Д-1").querySelector(".contract-register-status")).toHaveTextContent("Черновик");
    for (const deadline of container.querySelectorAll(".contract-register-deadline")) {
      expect(deadline).toHaveTextContent("Не указан");
    }
  });

  it("uses the project currency when formatting amounts", () => {
    const { container } = render(<ContractScheme projectId={21} currency="USD" contracts={[
      { id: 1, number: "Д-1", title: "Договор", amount: "123456.7" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} />);
    expect(contractRow(container, "Д-1").querySelector(".contract-register-amount")?.textContent).toBe("123\u00a0456,70 USD");
  });

  it("expands documents in the next table row and collapses by number or file count", () => {
    const onOpenDocument = vi.fn();
    const { container } = render(<ContractScheme projectId={22} contracts={[
      { id: 1, number: "Д-1", title: "Первый договор", linked_documents: [{ id: 91, name: "Исходный договор.pdf", source: "Загруженный файл" }] },
      { id: 2, number: "Д-2", title: "Второй договор", linked_documents: [{ id: 92, name: "Акт.pdf" }] },
    ]} onConnect={vi.fn()} onOpenDocument={onOpenDocument} />);
    const firstRow = contractRow(container, "Д-1");
    const number = within(firstRow).getByRole("button", { name: /Заказчик Д-1/ });
    fireEvent.click(number);
    expect(number).toHaveAttribute("aria-expanded", "true");
    expect(number).toHaveAttribute("aria-controls", "contract-files-22-1");
    expect(firstRow.nextElementSibling).toHaveClass("contract-register-expanded");
    expect(firstRow.nextElementSibling?.querySelector("td")).toHaveAttribute("colspan", "6");
    const panel = container.querySelector<HTMLElement>("#contract-files-22-1")!;
    fireEvent.click(within(panel).getByRole("button", { name: /Исходный договор.pdf/ }));
    expect(onOpenDocument).toHaveBeenCalledExactlyOnceWith(91);
    fireEvent.click(number);
    expect(container.querySelector(".contract-register-expanded")).not.toBeInTheDocument();
    const secondFiles = within(contractRow(container, "Д-2")).getByRole("button", { name: "Документы договора Д-2: 1" });
    fireEvent.click(secondFiles);
    expect(secondFiles).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("#contract-files-22-2")).toBeInTheDocument();
    fireEvent.click(number);
    expect(container.querySelectorAll(".contract-register-expanded")).toHaveLength(1);
    expect(secondFiles).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(within(firstRow).getByRole("button", { name: "Документы договора Д-1: 1" }));
    expect(container.querySelector(".contract-register-expanded")).not.toBeInTheDocument();
  });

  it("hides filtered-out detail without selecting another row", () => {
    const { container } = render(<ContractScheme projectId={23} contracts={[
      { id: 1, number: "Д-1", title: "Первый договор" },
      { id: 2, number: "Д-2", title: "Второй договор" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} />);
    expandContract(container, "Д-1");
    fireEvent.change(within(container).getByLabelText("Поиск договоров"), { target: { value: "Д-2" } });
    expect(container.querySelector(".contract-register-detail")).not.toBeInTheDocument();
    expect(within(contractRow(container, "Д-2")).getByRole("button", { name: /Заказчик Д-2/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("clears selected rows and filters when switching projects with repeated contract IDs", () => {
    const callbacks = { onConnect: vi.fn(), onOpenDocument: vi.fn() };
    const { container, rerender } = render(<ContractScheme projectId={24} contracts={[
      { id: 1, number: "СТАРЫЙ-1", title: "Старый договор", contract_kind: "supply" },
    ]} {...callbacks} />);
    expandContract(container, "СТАРЫЙ-1");
    fireEvent.change(within(container).getByLabelText("Поиск договоров"), { target: { value: "СТАРЫЙ" } });
    fireEvent.change(within(container).getByLabelText("Фильтр типа договора"), { target: { value: "supply" } });
    rerender(<ContractScheme projectId={25} contracts={[
      { id: 1, number: "НОВЫЙ-1", title: "Новый договор", contract_kind: "customer" },
    ]} {...callbacks} />);
    expect(within(container).getByLabelText("Поиск договоров")).toHaveValue("");
    expect(within(container).getByLabelText("Фильтр типа договора")).toHaveValue("all");
    expect(contractRow(container, "НОВЫЙ-1")).toBeInTheDocument();
    expect(container.querySelector(".contract-register-detail")).not.toBeInTheDocument();
  });

  it("connects the expanded contract using its actions", () => {
    const onConnect = vi.fn();
    const { container } = render(<ContractScheme projectId={26} contracts={[
      { id: 1, number: "ГП-1", title: "Генподряд" },
      { id: 2, number: "СП-2", title: "Подчинённый договор" },
    ]} onConnect={onConnect} onOpenDocument={vi.fn()} />);
    const panel = expandActions(container, "СП-2");
    const parentPicker = within(panel).getByLabelText("Вышестоящий договор");
    fireEvent.change(parentPicker, { target: { value: "1" } });
    expect(onConnect).toHaveBeenCalledExactlyOnceWith(1, 2);
    expect(parentPicker).toHaveValue("");
  });

  it("routes row drops of files or project documents to the proper contract", () => {
    const onDropFiles = vi.fn();
    const onDropDocuments = vi.fn();
    const { container } = render(<ContractScheme projectId={27} contracts={[
      { id: 8, number: "Д-8", title: "Вышестоящий договор" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} onDropDocuments={onDropDocuments} />);
    const row = contractRow(container, "Д-8");
    const pdf = new File(["contract"], "Подчинённый.pdf", { type: "application/pdf" });
    fireEvent.drop(row, { dataTransfer: fileTransfer([pdf]) });
    expect(onDropFiles).toHaveBeenCalledExactlyOnceWith([pdf], 8);
    fireEvent.drop(row, { dataTransfer: { files: [], types: [], getData: (type: string) => type === "application/x-pu-document-id" ? "105" : "" } });
    expect(onDropDocuments).toHaveBeenCalledExactlyOnceWith([105], 8);
    expect(onDropFiles).toHaveBeenCalledTimes(1);
  });

  it("shows the root drop overlay only for general areas, preserving row and file drop targets", () => {
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={28} contracts={[
      { id: 8, number: "Д-8", title: "Договор" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} onDropApplications={vi.fn()} onDropFinance={vi.fn()} />);
    expandActions(container, "Д-8");
    for (const target of [
      container.querySelector(".contract-register-name span")!,
      container.querySelector(".contract-application-drop span")!,
      container.querySelector(".contract-finance-drops strong")!,
    ]) {
      fireEvent.dragEnter(container.querySelector(".contract-scheme-head h2")!, { dataTransfer: fileTransfer([]) });
      expect(container.querySelector(".contract-drop-overlay")).toBeInTheDocument();
      fireEvent.dragEnter(target, { dataTransfer: fileTransfer([]) });
      expect(container.querySelector(".contract-drop-overlay")).not.toBeInTheDocument();
    }
    fireEvent.dragEnter(container.querySelector(".contract-scheme-head h2")!, { dataTransfer: fileTransfer([]) });
    expect(container.querySelector(".contract-drop-overlay")).toBeInTheDocument();
    const pdf = new File(["root"], "Корневой договор.pdf", { type: "application/pdf" });
    fireEvent.drop(container.querySelector(".contract-scheme")!, { dataTransfer: fileTransfer([pdf]) });
    expect(onDropFiles).toHaveBeenCalledExactlyOnceWith([pdf], undefined);
    expect(container.querySelector(".contract-drop-overlay")).not.toBeInTheDocument();
  });

  it("preserves graph node drop targets when dragging over nested content", () => {
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={29} contracts={[
      { id: 8, number: "Д-8", title: "Договор" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} />);
    fireEvent.click(within(container).getByRole("button", { name: "Схема связей" }));
    const nodeNumber = container.querySelector(".contract-scheme-node strong")!;
    fireEvent.dragEnter(nodeNumber, { dataTransfer: fileTransfer([]) });
    expect(container.querySelector(".contract-drop-overlay")).not.toBeInTheDocument();
    const pdf = new File(["child"], "Подчинённый договор.pdf", { type: "application/pdf" });
    fireEvent.drop(nodeNumber, { dataTransfer: fileTransfer([pdf]) });
    expect(onDropFiles).toHaveBeenCalledExactlyOnceWith([pdf], 8);
  });

  it("connects parent to child and opens linked documents", () => {
    const onConnect = vi.fn();
    const onOpenDocument = vi.fn();
    const { container } = render(<ContractScheme projectId={7} contracts={[
      { id: 1, number: "ГП-1", title: "Генподряд", contract_kind: "prime_reference" },
      { id: 2, number: "СП-2", title: "Наш договор", contract_kind: "revenue_subcontract", linked_documents: [{ id: 9, name: "Договор.pdf" }] },
    ]} onConnect={onConnect} onOpenDocument={onOpenDocument} />);
    fireEvent.click(within(container).getByRole("button", { name: "Схема связей" }));
    expect(container.querySelector(".contract-scheme-detail")).not.toBeInTheDocument();
    fireEvent.click(within(container).getByRole("button", { name: /Связать договоры/ }));
    fireEvent.click(within(container).getByRole("button", { name: /Генподряд ГП-1/ }));
    fireEvent.click(within(container).getByRole("button", { name: /Наш договор СП-2/ }));
    expect(onConnect).toHaveBeenCalledWith(1, 2);
    fireEvent.click(within(container).getByRole("button", { name: /Наш договор СП-2/ }));
    const detail = container.querySelector<HTMLElement>(".contract-scheme-detail")!;
    fireEvent.click(within(detail).getByRole("button", { name: /Договор.pdf/ }));
    expect(onOpenDocument).toHaveBeenCalledWith(9);
    fireEvent.click(within(container).getByRole("button", { name: "К таблице" }));
    expect(container.querySelector(".contract-scheme-scroll")).not.toBeInTheDocument();
    expect(contractRow(container, "СП-2").nextElementSibling).toHaveClass("contract-register-expanded");
  });

  it("separates contracts saved at the same coordinates", () => {
    window.localStorage.setItem("pu-contract-scheme:8", JSON.stringify({ 1: { x: 30, y: 30 }, 2: { x: 30, y: 30 } }));
    const { container } = render(<ContractScheme projectId={8} contracts={[
      { id: 1, number: "ГП-1", title: "Генподряд", contract_kind: "prime_reference" },
      { id: 2, number: "СП-2", title: "Исполнитель", contract_kind: "downstream_subcontract", parent_contract_id: 1 },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} />);
    fireEvent.click(within(container).getByRole("button", { name: "Схема связей" }));
    const nodes = Array.from(container.querySelectorAll<HTMLElement>(".contract-scheme-node"));
    expect(`${nodes[0].style.left}:${nodes[0].style.top}`).not.toBe(`${nodes[1].style.left}:${nodes[1].style.top}`);
    expect(within(container).getByText("← ГП-1")).toBeInTheDocument();
  });

  it("accepts application files on the selected contract", () => {
    const onDropApplications = vi.fn();
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={9} contracts={[
      { id: 3, number: "Д-3", title: "Договор", contract_kind: "customer" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropApplications={onDropApplications} onDropFiles={onDropFiles} />);
    const panel = expandContract(container, "Д-3");
    const file = new File(["application"], "Приложение №1.txt", { type: "text/plain" });
    const dropArea = panel.querySelector(".contract-application-drop")!;
    expect(dropArea).toHaveTextContent("Добавить файл");
    fireEvent.drop(dropArea, { dataTransfer: fileTransfer([file]) });
    expect(onDropApplications).toHaveBeenCalledExactlyOnceWith([file], 3);
    expect(onDropFiles).not.toHaveBeenCalled();
  });

  it("offers a picker and accepts legacy or unknown application extensions", () => {
    const onDropApplications = vi.fn();
    const { container } = render(<ContractScheme projectId={9} contracts={[
      { id: 3, number: "Д-3", title: "Договор", contract_kind: "customer" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropApplications={onDropApplications} />);
    fireEvent.click(container.querySelector(".contract-register-open")!);
    const picker = container.querySelector<HTMLInputElement>('[aria-label="Выбрать приложения к договору"]')!;
    expect(picker.accept).toBe("");
    expect(picker.multiple).toBe(true);
    const legacy = new File(["legacy"], "Разрешение.doc", { type: "application/msword" });
    const engineering = new File(["model"], "Модель.ifc", { type: "application/octet-stream" });
    fireEvent.change(picker, { target: { files: [legacy, engineering] } });
    expect(onDropApplications).toHaveBeenCalledWith([legacy, engineering], 3);
  });

  it("accepts Windows Explorer application drops exposed through DataTransfer.items", () => {
    const onDropApplications = vi.fn();
    const { container } = render(<ContractScheme projectId={9} contracts={[
      { id: 3, number: "Д-3", title: "Договор", contract_kind: "customer" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropApplications={onDropApplications} />);
    fireEvent.click(container.querySelector(".contract-register-open")!);
    const legacy = new File(["legacy"], "Разрешение.doc", { type: "application/msword" });
    fireEvent.drop(container.querySelector(".contract-application-drop")!, {
      dataTransfer: { files: [], items: [{ kind: "file", getAsFile: () => legacy }] },
    });
    expect(onDropApplications).toHaveBeenCalledWith([legacy], 3);
  });

  it.each([
    ["schedule", "ГПР", "register"], ["budget", "Бюджет", "register"], ["cash-flow", "ДДС", "register"],
    ["schedule", "ГПР", "scheme"], ["budget", "Бюджет", "scheme"], ["cash-flow", "ДДС", "scheme"],
  ] as const)("routes %s (%s) drops, picker selections and Windows items in %s", (kind, title, view) => {
    const onDropFinance = vi.fn();
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={10} contracts={[
      { id: 4, number: "СП-4", title: "Субподряд", contract_kind: "downstream_subcontract" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFinance={onDropFinance} onDropFiles={onDropFiles} />);
    let panel: HTMLElement;
    if (view === "scheme") {
      fireEvent.click(within(container).getByRole("button", { name: "Схема связей" }));
      fireEvent.click(within(container).getByRole("button", { name: /Субподрядчик СП-4/ }));
      panel = container.querySelector<HTMLElement>(".contract-register-detail")!;
      fireEvent.click(within(panel).getByText("Действия с договором", { selector: "summary" }));
    } else {
      panel = expandActions(container, "СП-4");
    }
    const picker = within(panel).getByLabelText<HTMLInputElement>(`Загрузить ${title} к договору`);
    expect(picker.multiple).toBe(true);
    const file = new File(["work;date\nЭтап;2026-09-01"], `${title}.csv`, { type: "text/csv" });
    fireEvent.drop(picker.closest("label")!, { dataTransfer: fileTransfer([file]) });
    expect(onDropFinance).toHaveBeenNthCalledWith(1, [file], 4, kind);
    fireEvent.change(picker, { target: { files: [file] } });
    expect(onDropFinance).toHaveBeenNthCalledWith(2, [file], 4, kind);
    fireEvent.drop(picker.closest("label")!, {
      dataTransfer: { files: [], items: [{ kind: "file", getAsFile: () => file }] },
    });
    expect(onDropFinance).toHaveBeenNthCalledWith(3, [file], 4, kind);
    expect(onDropFinance).toHaveBeenCalledTimes(3);
    expect(onDropFiles).not.toHaveBeenCalled();
  });

  it("accepts a photographed contract from the file picker", () => {
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={11} contracts={[]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} />);
    const input = container.querySelector<HTMLInputElement>('.contract-upload-button input[type="file"]')!;
    expect(input.accept).toContain(".jpg");
    expect(input).toHaveAccessibleName("Выбрать файлы договоров");
    expect(input.multiple).toBe(true);
    const photo = new File(["photo"], "Договор-фото.jpg", { type: "image/jpeg" });
    fireEvent.change(input, { target: { files: [photo] } });
    expect(onDropFiles).toHaveBeenCalledWith([photo], undefined);
    expect(container.querySelector('[role="status"]')).toHaveTextContent("Получено файлов: 1");
  });

  it("accepts a contract dropped anywhere on the constructor", () => {
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={12} contracts={[]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} />);
    const pdf = new File(["contract"], "Договор.pdf", { type: "application/pdf" });
    fireEvent.drop(container.querySelector(".contract-scheme")!, { dataTransfer: { files: [pdf], types: ["Files"], getData: () => "" } });
    expect(onDropFiles).toHaveBeenCalledWith([pdf], undefined);
    expect(container.querySelector('[role="status"]')).toHaveTextContent("Передаю на загрузку и анализ");
  });

  it("accepts Windows Explorer drops exposed through DataTransfer.items", () => {
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={14} contracts={[]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} />);
    const pdf = new File(["scan"], "Б-УЗП130-02-2026.pdf", { type: "application/pdf" });
    fireEvent.drop(container.querySelector(".contract-scheme")!, {
      dataTransfer: {
        files: [], types: ["Files"], getData: () => "",
        items: [{ kind: "file", getAsFile: () => pdf }],
      },
    });
    expect(onDropFiles).toHaveBeenCalledWith([pdf], undefined);
  });

  it("offers deletion only inside expanded row actions and preserves graph node deletion", () => {
    const onDelete = vi.fn();
    const { container } = render(<ContractScheme projectId={13} contracts={[
      { id: 5, number: "Д-5", title: "Удаляемый договор", contract_kind: "customer" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDelete={onDelete} />);
    expect(within(container).queryByRole("button", { name: /Удалить договор/ })).not.toBeInTheDocument();
    const panel = expandContract(container, "Д-5");
    expect(panel.querySelector<HTMLDetailsElement>(".contract-row-actions")!.open).toBe(false);
    expect(within(panel).getByRole("button", { name: "Удалить договор" })).not.toBeVisible();
    fireEvent.click(within(panel).getByText("Действия с договором", { selector: "summary" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Удалить договор" }));
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 5, number: "Д-5" }));
    fireEvent.click(within(container).getByRole("button", { name: "Схема связей" }));
    fireEvent.click(within(container).getByRole("button", { name: "Удалить договор Д-5" }));
    expect(onDelete).toHaveBeenCalledTimes(2);
  });

  it("archives a contract separately from physical deletion", () => {
    const onArchive = vi.fn();
    const onDelete = vi.fn();
    const { container } = render(<ContractScheme projectId={15} contracts={[
      { id: 6, number: "Д-6", title: "Связанный договор", status: "active" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDelete={onDelete} onArchive={onArchive} />);
    const panel = expandActions(container, "Д-6");
    fireEvent.click(within(panel).getByRole("button", { name: "Архивировать договор" }));
    expect(onArchive).toHaveBeenCalledWith(expect.objectContaining({ id: 6 }));
    expect(onDelete).not.toHaveBeenCalled();
  });
});
