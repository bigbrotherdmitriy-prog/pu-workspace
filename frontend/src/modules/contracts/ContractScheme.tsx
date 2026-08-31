import { useEffect, useMemo, useRef, useState } from "react";
import { FileText, Link2, Move, Network, X } from "lucide-react";
import { buildContractTree } from "./contractTree";

export type SchemeDocument = { id: number; name: string; source?: string; source_url?: string };
export type SchemeContract = {
  id: number;
  number: string;
  title: string;
  counterparty?: string;
  contract_kind?: string;
  parent_contract_id?: number;
  linked_documents?: SchemeDocument[];
};

type Point = { x: number; y: number };
type Props = {
  projectId: number;
  contracts: SchemeContract[];
  onConnect: (parentId: number, childId: number) => void;
  onOpenDocument: (documentId: number) => void;
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

function kindLabel(kind?: string) {
  if (kind === "prime_reference") return "Генподряд";
  if (kind === "revenue_subcontract") return "Наш договор";
  if (kind === "downstream_subcontract") return "Субподрядчик";
  if (kind === "supply") return "Поставщик";
  return "Заказчик";
}

export function ContractScheme({ projectId, contracts, onConnect, onOpenDocument }: Props) {
  const storageKey = `pu-contract-scheme:${projectId}`;
  const [positions, setPositions] = useState<Record<number, Point>>(() => defaultPositions(contracts));
  const [connectingFrom, setConnectingFrom] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const drag = useRef<{ id: number; dx: number; dy: number } | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem(storageKey);
    const restored = saved ? JSON.parse(saved) as Record<number, Point> : {};
    const defaults = defaultPositions(contracts);
    setPositions(Object.fromEntries(contracts.map((item) => [item.id, restored[item.id] || defaults[item.id]])));
  }, [storageKey, contracts]);

  useEffect(() => {
    if (contracts.length) window.localStorage.setItem(storageKey, JSON.stringify(positions));
  }, [storageKey, positions, contracts.length]);

  const selected = contracts.find((item) => item.id === selectedId);
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

  return <section className="card contract-scheme">
    <header className="contract-scheme-head">
      <div><span className="eyebrow">СХЕМА ДОГОВОРОВ</span><h2>Конструктор связей</h2><p>Перемещайте блоки. Чтобы провести линию, выберите вышестоящий договор, затем подчинённый.</p></div>
      <div className="contract-scheme-tools">
        <button className={connectingFrom !== null ? "selected" : ""} onClick={() => setConnectingFrom(connectingFrom === null ? -1 : null)}><Link2 /> {connectingFrom === null ? "Связать договоры" : connectingFrom === -1 ? "Выберите вышестоящий" : "Теперь выберите подчинённый"}</button>
        <button className="secondary" onClick={() => setPositions(defaultPositions(contracts))}><Network /> Выровнять</button>
      </div>
    </header>
    <div className="contract-scheme-scroll">
      <div className="contract-scheme-canvas" style={{ width: canvasWidth, height: canvasHeight }} onPointerMove={(event) => {
        if (!drag.current) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        setPositions((current) => ({ ...current, [drag.current!.id]: { x: Math.max(12, event.clientX - bounds.left - drag.current!.dx), y: Math.max(12, event.clientY - bounds.top - drag.current!.dy) } }));
      }} onPointerUp={() => { drag.current = null; }} onPointerLeave={() => { drag.current = null; }}>
        <svg className="contract-scheme-lines" width={canvasWidth} height={canvasHeight} aria-label="Связи договоров">
          <defs><marker id="contract-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
          {links.map(({ child, parent, own }) => <path key={child.id} d={`M ${parent.x + nodeWidth / 2} ${parent.y + nodeHeight} C ${parent.x + nodeWidth / 2} ${parent.y + nodeHeight + 36}, ${own.x + nodeWidth / 2} ${own.y - 36}, ${own.x + nodeWidth / 2} ${own.y}`} markerEnd="url(#contract-arrow)" />)}
        </svg>
        {contracts.map((contract) => {
          const point = positions[contract.id] || { x: 20, y: 20 };
          const connectMode = connectingFrom !== null;
          return <article key={contract.id} className={`contract-scheme-node ${selectedId === contract.id ? "selected" : ""} ${connectingFrom === contract.id ? "link-source" : ""}`} data-kind={contract.contract_kind || "customer"} style={{ left: point.x, top: point.y }}>
            <button className="contract-node-drag" aria-label={`Переместить договор ${contract.number}`} onPointerDown={(event) => {
              const bounds = event.currentTarget.parentElement!.getBoundingClientRect();
              drag.current = { id: contract.id, dx: event.clientX - bounds.left, dy: event.clientY - bounds.top };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}><Move /></button>
            <button className="contract-node-open" onClick={() => connectMode ? chooseForConnection(contract.id) : setSelectedId(contract.id)}>
              <span>{kindLabel(contract.contract_kind)}</span><strong>{contract.number}</strong><small>{contract.counterparty || contract.title}</small>
              <b>{contract.linked_documents?.length || 0} док.</b>
            </button>
          </article>;
        })}
      </div>
    </div>
    {connectingFrom === -1 && <p className="contract-scheme-hint">Нажмите на блок вышестоящего договора.</p>}
    {connectingFrom && connectingFrom > 0 && <p className="contract-scheme-hint">Выбран вышестоящий договор №{contracts.find((item) => item.id === connectingFrom)?.number}. Теперь нажмите на подчинённый блок.</p>}
    {selected && <aside className="contract-scheme-detail">
      <button className="icon-button" aria-label="Закрыть договор" onClick={() => setSelectedId(null)}><X /></button>
      <span className="eyebrow">{kindLabel(selected.contract_kind)}</span><h3>{selected.number} — {selected.title}</h3><p>{selected.counterparty || "Контрагент не указан"}</p>
      <h4>Привязанные документы</h4>
      <div className="contract-scheme-documents">{selected.linked_documents?.map((document) => <button key={document.id} onClick={() => onOpenDocument(document.id)}><FileText /><span><strong>{document.name}</strong><small>{document.source || "Документ проекта"}</small></span></button>)}
        {!selected.linked_documents?.length && <p>Документы ещё не привязаны. Добавьте документ-источник в карточке договора ниже.</p>}
      </div>
    </aside>}
  </section>;
}
