import { useMemo, useState } from "react";
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
};

type DocumentOption = { id: number; name: string; mime_type?: string; source: string };
type ContractOption = { id: number; number: string; title: string };
type Props = {
  documents: DocumentOption[];
  contracts: ContractOption[];
  onDiscover: (documentIds: number[]) => Promise<BulkContractProposal[]>;
  onImport: (proposals: BulkContractProposal[]) => Promise<number>;
};

const kindLabel: Record<string, string> = {
  prime_reference: "Генподрядный договор — корень",
  customer: "Прямой договор — корень",
  revenue_subcontract: "Наш договор под генподрядным",
  downstream_subcontract: "Субподрядчик / субсубподрядчик",
  supply: "Поставщик",
};

export function ContractBulkImportWizard({ documents, contracts, onDiscover, onImport }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [proposals, setProposals] = useState<BulkContractProposal[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const files = useMemo(() => documents.filter((item) => !item.mime_type?.includes("folder") &&
    (!query.trim() || item.name.toLocaleLowerCase("ru-RU").includes(query.trim().toLocaleLowerCase("ru-RU")))), [documents, query]);
  const needsParent = (kind: string) => !["prime_reference", "customer"].includes(kind);
  const invalid = proposals.some((item) => !item.number.trim() || !item.title.trim() ||
    (needsParent(item.contract_kind) && !item.parent_document_id && !item.parent_contract_id));

  async function analyze() {
    if (!selected.length) return;
    setBusy(true); setError("");
    try { setProposals(await onDiscover(selected)); }
    catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function apply() {
    setBusy(true); setError("");
    try {
      await onImport(proposals.filter((item) => !item.already_linked));
      setOpen(false); setSelected([]); setProposals([]);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="card contract-bulk-launch">
    <div><FileSearch /><span><strong>Массовый разбор договоров</strong><small>Выберите файлы, проверьте распознанные роли и импортируйте готовое дерево.</small></span></div>
    <button onClick={() => setOpen(true)}>Выбрать все договоры</button>
    {open && <div className="contract-bulk-backdrop" role="dialog" aria-modal="true" aria-label="Массовый разбор договоров">
      <div className="contract-bulk-dialog">
        <header><div><span className="eyebrow">МАССОВЫЙ МАСТЕР</span><h2>Файлы → проверка → дерево договоров</h2><p>Система ничего не привяжет до вашего подтверждения.</p></div><button className="icon-button" aria-label="Закрыть" onClick={() => setOpen(false)}><X /></button></header>
        {!proposals.length ? <>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по названию файла" />
          <div className="contract-bulk-tools"><button className="secondary" onClick={() => setSelected(files.map((item) => item.id))}>Выбрать все найденные ({files.length})</button><button className="secondary" onClick={() => setSelected([])}>Снять выбор</button><b>Выбрано: {selected.length}</b></div>
          <div className="contract-bulk-files">{files.slice(0, 300).map((item) => <label key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} /><span><strong>{item.name}</strong><small>{item.source}</small></span></label>)}</div>
          <button disabled={!selected.length || busy} onClick={analyze}>{busy ? "Анализирую выбранные файлы…" : `Проанализировать ${selected.length} файлов`}</button>
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
          <div className="contract-bulk-actions"><button className="secondary" onClick={() => setProposals([])}>Назад к файлам</button><button disabled={invalid || busy || proposals.every((item) => item.already_linked)} onClick={apply}>{busy ? "Создаю дерево…" : "Создать и привязать всё дерево"}</button></div>
        </>}
        {error && <p className="error">{error}</p>}
      </div>
    </div>}
  </section>;
}
