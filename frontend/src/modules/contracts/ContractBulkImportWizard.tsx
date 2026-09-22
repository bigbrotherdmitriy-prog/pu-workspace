import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { createPortal } from "react-dom";
import { FileSearch, Network, X } from "lucide-react";

export type BulkContractProposal = {
  document_id: number;
  document_name: string;
  number: string;
  title: string;
  counterparty?: string;
  contract_kind: string;
  parent_document_id?: number;
  parent_contract_id?: number;
  confidence: number;
  evidence: string[];
  already_linked: boolean;
  linked_contract_id?: number;
};

type DocumentOption = { id: number; name: string; mime_type?: string; source: string };
type ContractOption = { id: number; number: string; title: string };
export type BulkContractCandidate = BulkContractProposal & { reason?: string };
export type ContractDiscoveryProgress = { completed: number; total: number; jobsCompleted: number; jobsTotal: number };
type Props = {
  documents: DocumentOption[];
  contracts: ContractOption[];
  onFindCandidates: (onProgress: (progress: ContractDiscoveryProgress) => void) => Promise<{ candidates: BulkContractCandidate[]; remaining: BulkContractCandidate[] }>;
  onImport: (proposals: BulkContractProposal[]) => Promise<number>;
  incomingProposals?: BulkContractProposal[];
  onIncomingConsumed?: () => void;
};

const kindLabel: Record<string, string> = {
  prime_reference: "Генподрядный договор — корень",
  customer: "Прямой договор — корень",
  revenue_subcontract: "Наш договор под генподрядным",
  downstream_subcontract: "Субподрядчик / субсубподрядчик",
  supply: "Поставщик",
};

export function ContractBulkImportWizard({ documents, contracts, onFindCandidates, onImport, incomingProposals, onIncomingConsumed }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [proposals, setProposals] = useState<BulkContractProposal[]>([]);
  const [candidates, setCandidates] = useState<BulkContractCandidate[]>([]);
  const [remaining, setRemaining] = useState<BulkContractCandidate[]>([]);
  const [scanComplete, setScanComplete] = useState(false);
  const [progress, setProgress] = useState<ContractDiscoveryProgress>({ completed: 0, total: 0, jobsCompleted: 0, jobsTotal: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const filteredCandidates = useMemo(() => candidates.filter((item) => !query.trim() || item.document_name.toLocaleLowerCase("ru-RU").includes(query.trim().toLocaleLowerCase("ru-RU"))), [candidates, query]);
  const filteredRemaining = useMemo(() => remaining.filter((item) => !query.trim() || item.document_name.toLocaleLowerCase("ru-RU").includes(query.trim().toLocaleLowerCase("ru-RU"))), [remaining, query]);
  const needsParent = (kind: string) => !["prime_reference", "customer"].includes(kind);
  const invalid = proposals.some((item) => !item.number.trim() || !item.title.trim() ||
    (needsParent(item.contract_kind) && !item.parent_document_id && !item.parent_contract_id));

  useEffect(() => {
    if (!incomingProposals?.length) return;
    setProposals(incomingProposals); setOpen(true); setError("");
    onIncomingConsumed?.();
  }, [incomingProposals, onIncomingConsumed]);

  async function scan() {
    setBusy(true); setError(""); setScanComplete(false); setCandidates([]); setRemaining([]); setSelected([]);
    try {
      const result = await onFindCandidates(setProgress);
      setCandidates(result.candidates); setRemaining(result.remaining);
      setSelected(result.candidates.map((item) => item.document_id)); setScanComplete(true);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  function analyze() {
    if (!selected.length) return;
    const byId = new Map([...candidates, ...remaining].map((item) => [item.document_id, item]));
    setProposals(selected.map((id) => byId.get(id)).filter((item): item is BulkContractCandidate => Boolean(item)));
  }

  async function apply() {
    setBusy(true); setError("");
    try {
      await onImport(proposals);
      setOpen(false); setSelected([]); setProposals([]);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="card contract-bulk-launch">
    <div><FileSearch /><span><strong>Массовый разбор договоров</strong><small>Выберите файлы, проверьте распознанные роли и импортируйте готовое дерево.</small></span></div>
    <button onClick={() => { setOpen(true); if (!busy) void scan(); }}>Найти договоры</button>
    {open && createPortal(<div className="contract-bulk-backdrop" role="dialog" aria-modal="true" aria-label="Массовый разбор договоров">
      <div className="contract-bulk-dialog">
        <header><div><span className="eyebrow">МАССОВЫЙ МАСТЕР</span><h2>Файлы → проверка → дерево договоров</h2><p>Система ничего не привяжет до вашего подтверждения.</p></div><button className="icon-button" aria-label="Закрыть" onClick={() => setOpen(false)}><X /></button></header>
        {!proposals.length ? <>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по названию файла" />
          {busy && <div className="contract-bulk-progress" role="status">Обработано {progress.completed} из {progress.total || documents.length} · партий {progress.jobsCompleted} из {progress.jobsTotal || "…"}</div>}
          {!busy && !scanComplete && <button onClick={() => void scan()}>Найти кандидатов</button>}
          {scanComplete && <>
            <div className="contract-bulk-tools"><button className="secondary" onClick={() => setSelected(candidates.map((item) => item.document_id))}>Выбрать кандидатов ({candidates.length})</button><button className="secondary" onClick={() => setSelected([])}>Снять выбор</button><b>Выбрано: {selected.length}</b></div>
            <section className="contract-bulk-group"><h3>Найдено кандидатов: {candidates.length}</h3><div className="contract-bulk-files">{filteredCandidates.map((item) => <CandidateRow item={item} selected={selected} setSelected={setSelected} key={item.document_id} />)}</div></section>
            <details className="contract-bulk-group"><summary>Остальные документы: {remaining.length}</summary><p>Они не выбраны. Добавьте файл вручную, только если уверены, что это договор.</p><div className="contract-bulk-files">{filteredRemaining.map((item) => <CandidateRow item={item} selected={selected} setSelected={setSelected} key={item.document_id} />)}</div></details>
            <button disabled={!selected.length} onClick={analyze}>{`Проверить ${selected.length} предложений`}</button>
          </>}
        </> : <>
          <div className="contract-bulk-tree-head"><Network /><span><strong>Проверьте порядок договоров</strong><small>Для каждого нижнего договора укажите непосредственный вышестоящий.</small></span></div>
          <div className="contract-bulk-proposals">{proposals.map((item, index) => <article className={item.already_linked ? "linked" : ""} key={item.document_id}>
            <span className="contract-bulk-index">{index + 1}</span><div className="contract-bulk-fields">
              <small>{Math.round(item.confidence * 100)}% · {item.document_name}{item.already_linked ? " · уже привязан" : ""}</small>
              <input value={item.number} onChange={(event) => setProposals((rows) => rows.map((row) => row.document_id === item.document_id ? { ...row, number: event.target.value } : row))} aria-label={`Номер ${item.document_name}`} />
              <input value={item.title} onChange={(event) => setProposals((rows) => rows.map((row) => row.document_id === item.document_id ? { ...row, title: event.target.value } : row))} aria-label={`Название ${item.document_name}`} />
              <input value={item.counterparty || ""} onChange={(event) => setProposals((rows) => rows.map((row) => row.document_id === item.document_id ? { ...row, counterparty: event.target.value } : row))} placeholder="Контрагент" />
              <select value={item.contract_kind} onChange={(event) => setProposals((rows) => rows.map((row) => row.document_id === item.document_id ? { ...row, contract_kind: event.target.value, parent_document_id: undefined, parent_contract_id: undefined } : row))}>{Object.entries(kindLabel).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
              {needsParent(item.contract_kind) && <select value={item.parent_document_id ? `document:${item.parent_document_id}` : item.parent_contract_id ? `contract:${item.parent_contract_id}` : ""} onChange={(event) => { const [type, id] = event.target.value.split(":"); setProposals((rows) => rows.map((row) => row.document_id === item.document_id ? { ...row, parent_document_id: type === "document" ? Number(id) : undefined, parent_contract_id: type === "contract" ? Number(id) : undefined } : row)); }}>
                <option value="">Выберите вышестоящий договор</option>
                {contracts.map((parent) => <option value={`contract:${parent.id}`} key={`c-${parent.id}`}>{parent.number} — существующий</option>)}
                {proposals.filter((parent) => parent.document_id !== item.document_id).map((parent) => <option value={`document:${parent.document_id}`} key={`d-${parent.document_id}`}>{parent.number} — из выбранных</option>)}
              </select>}
              <small>{item.evidence.join("; ")}</small>
            </div>
          </article>)}</div>
          <div className="contract-bulk-actions"><button className="secondary" onClick={() => setProposals([])}>Назад к файлам</button><button disabled={invalid || busy} onClick={apply}>{busy ? "Сохраняю дерево…" : proposals.some((item) => item.already_linked) ? "Подтвердить и обновить всё дерево" : "Создать и привязать всё дерево"}</button></div>
        </>}
        {error && <p className="error">{error}</p>}
      </div>
    </div>, document.body)}
  </section>;
}

function CandidateRow({ item, selected, setSelected }: { item: BulkContractCandidate; selected: number[]; setSelected: Dispatch<SetStateAction<number[]>> }) {
  return <label key={item.document_id}>
    <input type="checkbox" checked={selected.includes(item.document_id)} onChange={(event) => setSelected((current) => event.target.checked ? [...new Set([...current, item.document_id])] : current.filter((id) => id !== item.document_id))} />
    <span><strong>{item.document_name}</strong><small>{item.reason || item.evidence.join("; ")}</small></span>
  </label>;
}
