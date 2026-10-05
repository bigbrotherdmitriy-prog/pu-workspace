import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Archive, ChevronDown, FileText, FileUp, Link2, ListTree, Move, Network, Search, Trash2, X } from "lucide-react";
import { buildContractTree } from "./contractTree";

export type SchemeDocument = { id: number; name: string; source?: string; source_url?: string };
export type SchemeContract = {
  id: number;
  number: string;
  title: string;
  counterparty?: string;
  contract_kind?: string;
  parent_contract_id?: number;
  status?: string;
  amount?: number | string | null;
  performed_to?: string | null;
  warranty_until?: string | null;
  linked_documents?: SchemeDocument[];
};

type Point = { x: number; y: number };
type Props = {
  projectId: number;
  contracts: SchemeContract[];
  onConnect: (parentId: number, childId: number) => void;
  onOpenDocument: (documentId: number) => void;
  onDelete?: (contract: SchemeContract) => void;
  onArchive?: (contract: SchemeContract) => void;
  onDropDocuments?: (documentIds: number[], parentContractId?: number) => void;
  onDropFiles?: (files: File[], parentContractId?: number) => void;
  onDropApplications?: (files: File[], contractId: number) => void;
  onDropFinance?: (files: File[], contractId: number, kind: "schedule" | "budget" | "cash-flow") => void;
  operationStatus?: string;
  currency?: string;
  actions?: ReactNode;
  renderDetails?: (contract: SchemeContract, packageContent: ReactNode) => ReactNode;
};

const nodeWidth = 230;
const nodeHeight = 112;

function defaultPositions(contracts: SchemeContract[]): Record<number, Point> {
  const rows = buildContractTree(contracts);
  const counters = new Map<number, number>();
  return Object.fromEntries(rows.map(({ item, depth }) => {
    const column = counters.get(depth) || 0;
    counters.set(depth, column + 1);
    return [item.id, { x: 34 + column * 270, y: 34 + depth * 170 }];
  }));
}

function restoreWithoutOverlaps(contracts: SchemeContract[], restored: Record<number, Point>) {
  const defaults = defaultPositions(contracts);
  const result: Record<number, Point> = {};
  const occupied: Point[] = [];
  for (const contract of contracts) {
    let point = restored[contract.id] || defaults[contract.id] || { x: 34, y: 34 };
    if (occupied.some((other) => Math.abs(other.x - point.x) < nodeWidth - 20 && Math.abs(other.y - point.y) < nodeHeight - 20)) {
      point = defaults[contract.id] || point;
      while (occupied.some((other) => Math.abs(other.x - point.x) < nodeWidth - 20 && Math.abs(other.y - point.y) < nodeHeight - 20)) {
        point = { x: point.x + 270, y: point.y };
      }
    }
    result[contract.id] = point;
    occupied.push(point);
  }
  return result;
}

function kindLabel(kind?: string) {
  if (kind === "prime_reference") return "Генподряд";
  if (kind === "revenue_subcontract") return "Наш договор";
  if (kind === "downstream_subcontract") return "Субподрядчик";
  if (kind === "supply") return "Поставщик";
  return "Заказчик";
}

// Decimal strings from the API stay strings: displaying a NUMERIC(18,2) amount
// must not lose cents by converting it to a JavaScript number.
function contractAmount(amount: SchemeContract["amount"], currency: string) {
  if (amount === undefined || amount === null) return "Не указана";
  const text = typeof amount === "number" ? amount.toFixed(2) : amount;
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(text);
  if (!match) return "Не указана";
  const whole = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
  return `${match[1]}${whole},${(match[3] || "").padEnd(2, "0")} ${currency === "RUB" ? "₽" : currency}`;
}

const statusLabels: Record<string, string> = { draft: "Черновик", active: "Действует", completed: "Завершён", terminated: "Расторгнут", archived: "В архиве" };

export function ContractScheme({ projectId, contracts, onConnect, onOpenDocument, onDelete, onArchive, onDropDocuments, onDropFiles, onDropApplications, onDropFinance, operationStatus, currency = "RUB", actions, renderDetails }: Props) {
  const storageKey = `pu-contract-scheme:${projectId}`;
  const [view, setView] = useState<"register" | "scheme">("register");
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [positions, setPositions] = useState<Record<number, Point>>(() => defaultPositions(contracts));
  const [connectingFrom, setConnectingFrom] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dropTargetId, setDropTargetId] = useState<number | null>(null);
  const [dropFeedback, setDropFeedback] = useState("");
  const drag = useRef<{ id: number; dx: number; dy: number } | null>(null);
  const layoutRef = useRef<{ storageKey: string; hierarchy: string } | null>(null);
  const hierarchy = contracts.map((item) => `${item.id}:${item.parent_contract_id || 0}`).sort().join("|");

  useEffect(() => {
    setSelectedId(null);
    setQuery("");
    setKindFilter("all");
    setConnectingFrom(null);
    setDropTargetId(null);
    setDropFeedback("");
  }, [projectId]);

  useEffect(() => {
    const saved = window.localStorage.getItem(storageKey);
    let restored: Record<number, Point> = {};
    try { restored = saved ? JSON.parse(saved) as Record<number, Point> : {}; } catch { /* Ignore damaged local layout preferences. */ }
    const previous = layoutRef.current;
    const hierarchyChanged = previous?.storageKey === storageKey && previous.hierarchy !== hierarchy;
    setPositions(hierarchyChanged ? defaultPositions(contracts) : restoreWithoutOverlaps(contracts, restored));
    layoutRef.current = { storageKey, hierarchy };
  }, [storageKey, hierarchy]);

  useEffect(() => {
    if (contracts.length) window.localStorage.setItem(storageKey, JSON.stringify(positions));
  }, [storageKey, positions, contracts.length]);

  const registerRows = useMemo(() => {
    const term = query.trim().toLocaleLowerCase("ru-RU");
    return contracts.filter((item) =>
      (kindFilter === "all" || (item.contract_kind || "customer") === kindFilter)
      && (!term || [item.number, item.title, item.counterparty].some((value) => value?.toLocaleLowerCase("ru-RU").includes(term))),
    );
  }, [contracts, query, kindFilter]);
  const explicitlySelected = contracts.find((item) => item.id === selectedId);
  const selected = view === "register"
    ? registerRows.find((item) => item.id === selectedId)
    : explicitlySelected;
  const canvasHeight = Math.max(390, ...Object.values(positions).map((point) => point.y + nodeHeight + 45));
  const canvasWidth = Math.max(900, ...Object.values(positions).map((point) => point.x + nodeWidth + 45));
  const links = useMemo(() => contracts.flatMap((child) => {
    const parent = child.parent_contract_id ? positions[child.parent_contract_id] : undefined;
    const own = positions[child.id];
    return parent && own ? [{ child, parent, own }] : [];
  }), [contracts, positions]);

  function chooseForConnection(id: number) {
    if (connectingFrom === null || connectingFrom === -1) {
      setConnectingFrom(id);
      return;
    }
    if (connectingFrom !== id) onConnect(connectingFrom, id);
    setConnectingFrom(null);
  }

  function deliverFiles(files: File[], parentContractId?: number) {
    if (!files.length) {
      setDropFeedback("Файл не получен. Перетащите его из Проводника или нажмите «Загрузить договор» и выберите вручную.");
      return;
    }
    setDropFeedback(`Получено файлов: ${files.length}. Передаю на загрузку и анализ…`);
    onDropFiles?.(files, parentContractId);
  }

  function filesFromTransfer(transfer: DataTransfer): File[] {
    const direct = Array.from(transfer.files || []);
    if (direct.length) return direct;
    return Array.from(transfer.items || [])
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file));
  }

  function acceptDrop(event: React.DragEvent, parentContractId?: number) {
    event.preventDefault(); event.stopPropagation(); setDropTargetId(null);
    const projectDocumentId = Number(event.dataTransfer.getData("application/x-pu-document-id"));
    if (projectDocumentId) { onDropDocuments?.([projectDocumentId], parentContractId); return; }
    const files = filesFromTransfer(event.dataTransfer);
    deliverFiles(files, parentContractId);
  }

  const packagePanel = selected && <>
    <div className="contract-register-doc-head"><h3>Документы · {selected.linked_documents?.length || 0}</h3>
      {onDropApplications && <label className="contract-application-drop" onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); }} onDrop={(event) => { event.preventDefault(); event.stopPropagation(); const files = filesFromTransfer(event.dataTransfer); if (files.length) onDropApplications(files, selected.id); }}>
        <FileUp /><span>Добавить файл</span>
        <input aria-label="Выбрать приложения к договору" type="file" multiple onChange={(event) => { const files = Array.from(event.target.files || []); if (files.length) onDropApplications(files, selected.id); event.currentTarget.value = ""; }} />
      </label>}
    </div>
    <div className="contract-scheme-documents">{selected.linked_documents?.map((document) => <button key={document.id} onClick={() => onOpenDocument(document.id)}><FileText /><span><strong>{document.name}</strong><small>{document.source || "Документ проекта"}</small></span></button>)}
      {!selected.linked_documents?.length && <p>Документы ещё не привязаны.</p>}
    </div>
    <details className="contract-row-actions"><summary>Действия с договором</summary>
    {!renderDetails && <div className="contract-register-link">
      <label><span>Подчинить договору</span><select aria-label="Вышестоящий договор" defaultValue="" onChange={(event) => { const parentId = Number(event.target.value); if (parentId) onConnect(parentId, selected.id); event.currentTarget.value = ""; }}>
        <option value="">Выберите вышестоящий договор</option>
        {contracts.filter((item) => item.id !== selected.id).map((item) => <option value={item.id} key={item.id}>{item.number} — {item.counterparty || item.title}</option>)}
      </select></label>
    </div>}
    {onDropFinance && <>
    <div className="contract-finance-drops">
      {([['schedule', 'ГПР', 'Этапы и сроки'], ['budget', 'Бюджет', 'Смета и план затрат'], ['cash-flow', 'ДДС', 'Платёжный календарь']] as const).map(([kind, title, hint]) =>
        <label key={kind} onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); }} onDrop={(event) => { event.preventDefault(); event.stopPropagation(); const files = filesFromTransfer(event.dataTransfer); if (files.length) onDropFinance(files, selected.id, kind); }}>
          <FileUp /><span><strong>{title}</strong><small>{hint}</small></span>
          <input aria-label={`Загрузить ${title} к договору`} type="file" multiple onChange={(event) => { const files = Array.from(event.target.files || []); if (files.length) onDropFinance(files, selected.id, kind); event.currentTarget.value = ""; }} />
        </label>)}
    </div>
    <p className="contract-finance-confirmation">После анализа откроется предварительный просмотр. ГПР, бюджет и ДДС изменятся только после вашего подтверждения.</p>
    </>}
    {!renderDetails && (onDelete || onArchive) && <div className="contract-scheme-delete-zone">
      {onArchive && selected.status !== "archived" && <button className="secondary" onClick={() => onArchive(selected)}><Archive /> Архивировать договор</button>}
      {onDelete && <button className="danger" onClick={() => onDelete(selected)}><Trash2 /> Удалить договор</button>}
    </div>}
    </details>
  </>;
  const detailPanel = selected && <div className="contract-register-detail" id={`contract-files-${projectId}-${selected.id}`}>
    {renderDetails ? renderDetails(selected, packagePanel) : packagePanel}
  </div>;

  return <section className={`card contract-scheme contract-workspace ${dropTargetId === -1 ? "drop-active" : ""}`} onDragEnter={(event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (event.dataTransfer.types.includes("Files")) setDropTargetId(target?.closest(".contract-register-row, .contract-scheme-node, .contract-application-drop, .contract-finance-drops") ? null : -1);
  }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTargetId(null); }} onDragOver={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } }} onDrop={(event) => acceptDrop(event)}>
    <div className="contract-scheme-head">
      <h2>{view === "register" ? "Реестр договоров" : "Схема связей"}</h2>
      <div className="contract-scheme-tools">
        {view === "scheme" && <button className="secondary" onClick={() => { setView("register"); setConnectingFrom(null); }}><ListTree /> К таблице</button>}
        {view === "register" && <button className="secondary" onClick={() => setView("scheme")}><Network /> Схема связей</button>}
        {actions}
        <label className="contract-upload-button"><FileUp /> Загрузить договор
          <input aria-label="Выбрать файлы договоров" type="file" accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.csv,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,image/png,image/jpeg,image/tiff,image/bmp,image/webp" multiple onChange={(event) => { deliverFiles(Array.from(event.target.files || [])); event.currentTarget.value = ""; }} />
        </label>
        {view === "scheme" && <><button className={connectingFrom !== null ? "selected" : ""} onClick={() => setConnectingFrom(connectingFrom === null ? -1 : null)}><Link2 /> {connectingFrom === null ? "Связать договоры" : connectingFrom === -1 ? "Выберите вышестоящий" : "Теперь выберите подчинённый"}</button>
        <button className="secondary" onClick={() => setPositions(defaultPositions(contracts))}><Network /> Выровнять</button></>}
      </div>
    </div>
    {dropTargetId === -1 && <div className="contract-drop-overlay" onDragLeave={() => setDropTargetId(null)} onDrop={(event) => acceptDrop(event)}><FileUp /> Отпустите файлы, чтобы загрузить договор</div>}
    {(operationStatus || dropFeedback) && <p className="contract-drop-feedback" role="status">{operationStatus || dropFeedback}</p>}
    {view === "register" && <div className="contract-register-table-wrap">
        <div className="contract-register-toolbar">
          <label><Search /><input aria-label="Поиск договоров" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Номер, название или контрагент" /></label>
          <select aria-label="Фильтр типа договора" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
            <option value="all">Все типы</option><option value="prime_reference">Генподряд</option><option value="customer">Заказчик</option><option value="revenue_subcontract">Наш договор</option><option value="downstream_subcontract">Субподрядчики</option><option value="supply">Поставщики</option>
          </select>
          <span className="contract-register-count">Найдено: <strong>{registerRows.length}</strong></span>
        </div>
        <table className="contract-register-table" aria-label="Реестр договоров">
          <thead><tr><th scope="col">Договор</th><th scope="col">Контрагент</th><th scope="col">Сумма</th><th scope="col">Срок окончания</th><th scope="col">Статус</th><th scope="col">Файлы</th></tr></thead>
          <tbody>{registerRows.map((item) => {
            const expanded = selected?.id === item.id;
            const toggle = () => setSelectedId(expanded ? null : item.id);
            return <Fragment key={item.id}><tr className={`contract-register-row ${expanded ? "selected" : ""}`} onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); setDropTargetId(item.id); }} onDrop={(event) => acceptDrop(event, item.id)}>
              <td data-label="Договор" className="contract-register-name"><button className="contract-register-open" aria-label={`${kindLabel(item.contract_kind)} ${item.number} ${item.title}`} aria-expanded={expanded} aria-controls={expanded ? `contract-files-${projectId}-${item.id}` : undefined} onClick={toggle}>{item.number}<ChevronDown /></button><span>{item.title}</span>
                {item.parent_contract_id && <small>К договору {contracts.find((parent) => parent.id === item.parent_contract_id)?.number || `№${item.parent_contract_id}`}</small>}
              </td>
              <td data-label="Контрагент"><span>{item.counterparty || "Не указан"}</span><small>{kindLabel(item.contract_kind)}</small></td>
              <td data-label="Сумма" className="contract-register-amount">{contractAmount(item.amount, currency)}</td>
              <td data-label="Срок окончания" className="contract-register-deadline"><span title="Окончание исполнения договора">{item.performed_to ? item.performed_to.slice(0, 10).split("-").reverse().join(".") : "Не указан"}</span></td>
              <td data-label="Статус"><span className={`contract-register-status ${item.status || "active"}`}>{statusLabels[item.status || "active"] || item.status}</span></td>
              <td data-label="Файлы"><button className="contract-register-files" aria-label={`Документы договора ${item.number}: ${item.linked_documents?.length || 0}`} aria-expanded={expanded} onClick={toggle}><FileText />{item.linked_documents?.length || 0}</button></td>
            </tr>{expanded && <tr className="contract-register-expanded"><td colSpan={6}>{detailPanel}</td></tr>}</Fragment>;
          })}</tbody>
        </table>
        {!registerRows.length && <div className="contract-register-empty"><Search /><strong>{contracts.length ? "Ничего не найдено" : "Договоров пока нет"}</strong><span>{contracts.length ? "Измените запрос или фильтр." : "Загрузите договор или создайте его вручную."}</span></div>}
    </div>}
    {view === "scheme" && <div className="contract-scheme-scroll">
      <div className={`contract-scheme-canvas ${dropTargetId === -1 ? "drop-active" : ""}`} style={{ width: canvasWidth, height: canvasHeight }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; if (event.target === event.currentTarget) setDropTargetId(-1); }} onDragLeave={(event) => { if (event.target === event.currentTarget) setDropTargetId(null); }} onDrop={(event) => acceptDrop(event)} onPointerMove={(event) => {
        if (!drag.current) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        setPositions((current) => ({ ...current, [drag.current!.id]: { x: Math.max(12, event.clientX - bounds.left - drag.current!.dx), y: Math.max(12, event.clientY - bounds.top - drag.current!.dy) } }));
      }} onPointerUp={() => { drag.current = null; }} onPointerLeave={() => { drag.current = null; }}>
        <svg className="contract-scheme-lines" width={canvasWidth} height={canvasHeight} aria-label="Связи договоров">
          <defs><marker id="contract-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
          {links.map(({ child, parent, own }) => <g key={child.id}><path className="contract-link-underlay" d={`M ${parent.x + nodeWidth / 2} ${parent.y + nodeHeight} C ${parent.x + nodeWidth / 2} ${parent.y + nodeHeight + 36}, ${own.x + nodeWidth / 2} ${own.y - 36}, ${own.x + nodeWidth / 2} ${own.y}`} /><path d={`M ${parent.x + nodeWidth / 2} ${parent.y + nodeHeight} C ${parent.x + nodeWidth / 2} ${parent.y + nodeHeight + 36}, ${own.x + nodeWidth / 2} ${own.y - 36}, ${own.x + nodeWidth / 2} ${own.y}`} markerEnd="url(#contract-arrow)" /></g>)}
        </svg>
        {contracts.map((contract) => {
          const point = positions[contract.id] || { x: 20, y: 20 };
          const connectMode = connectingFrom !== null;
          return <article key={contract.id} onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); setDropTargetId(contract.id); }} onDragLeave={() => setDropTargetId(null)} onDrop={(event) => acceptDrop(event, contract.id)} className={`contract-scheme-node ${selectedId === contract.id ? "selected" : ""} ${connectingFrom === contract.id ? "link-source" : ""} ${dropTargetId === contract.id ? "drop-target" : ""}`} data-kind={contract.contract_kind || "customer"} style={{ left: point.x, top: point.y }}>
            <button className="contract-node-drag" aria-label={`Переместить договор ${contract.number}`} onPointerDown={(event) => {
              const bounds = event.currentTarget.parentElement!.getBoundingClientRect();
              drag.current = { id: contract.id, dx: event.clientX - bounds.left, dy: event.clientY - bounds.top };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}><Move /></button>
            <button className="contract-node-open" onClick={() => connectMode ? chooseForConnection(contract.id) : setSelectedId(contract.id)}>
              <span>{kindLabel(contract.contract_kind)}</span><strong>{contract.number}</strong><small>{contract.counterparty || contract.title}</small>
              <b>{contract.linked_documents?.length || 0} док.</b>
              {contract.parent_contract_id && <em>← {contracts.find((item) => item.id === contract.parent_contract_id)?.number || `договор ${contract.parent_contract_id}`}</em>}
            </button>
            {onDelete && <button className="contract-node-delete" aria-label={`Удалить договор ${contract.number}`} title="Удалить договор" onClick={(event) => { event.stopPropagation(); onDelete(contract); }}><Trash2 /></button>}
          </article>;
        })}
      </div>
    </div>}
    {view === "scheme" && <><p className="contract-scheme-drop-hint"><FileUp /> На пустую область — новый корневой договор; прямо на карточку — договор будет предложен как подчинённый выбранному.</p>
    {connectingFrom === -1 && <p className="contract-scheme-hint">Нажмите на блок вышестоящего договора.</p>}
    {connectingFrom && connectingFrom > 0 && <p className="contract-scheme-hint">Выбран вышестоящий договор №{contracts.find((item) => item.id === connectingFrom)?.number}. Теперь нажмите на подчинённый блок.</p>}
    {selected && <aside className="contract-scheme-detail">
      <button className="icon-button" aria-label="Закрыть договор" onClick={() => setSelectedId(null)}><X /></button>
      {!renderDetails && <h3>{selected.number} — {selected.title}</h3>}
      {detailPanel}
    </aside>}</>}
  </section>;
}
