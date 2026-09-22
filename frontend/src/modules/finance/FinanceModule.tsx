import { useMemo, useRef, useState } from "react";
import type { FinanceDocumentCandidate, FinanceOverview } from "./types";
import { formatMoney } from "../../utils/numberFormat";

type ContractOption = { id: number; number: string; title: string; contract_kind?: string; parent_contract_id?: number };
type Props = {
  finance: FinanceOverview | null;
  candidates: FinanceDocumentCandidate[];
  contracts: ContractOption[];
  selectedContractId: number;
  onSelectContract: (id: number) => void;
  onPrepare: (kind: string) => void;
  onUseCandidate: (candidate: FinanceDocumentCandidate) => void;
  onUpload: () => void;
  onUploadFinance: (files: File[], contractId: number, kind: "budget" | "cash-flow") => void;
  onOpenSchedule: () => void;
  onReload: () => void;
};

const money = formatMoney;

export function FinanceModule({ finance, candidates, contracts, selectedContractId, onSelectContract, onPrepare, onUseCandidate, onUpload, onUploadFinance, onOpenSchedule, onReload }: Props) {
  const budgetInput = useRef<HTMLInputElement>(null);
  const cashFlowInput = useRef<HTMLInputElement>(null);
  const [candidateKind, setCandidateKind] = useState("all");
  const [minimumScore, setMinimumScore] = useState(50);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<number[]>([]);
  const visibleCandidates = useMemo(() => candidates.filter((candidate) =>
    !candidate.already_linked && candidate.score >= minimumScore &&
    (candidateKind === "all" || candidate.kind === candidateKind)
  ), [candidates, candidateKind, minimumScore]);
  const candidateCounts = useMemo(() => candidates.reduce<Record<string, number>>((result, candidate) => {
    result[candidate.kind] = (result[candidate.kind] || 0) + 1;
    return result;
  }, {}), [candidates]);
  const selectedCandidates = visibleCandidates.filter((candidate) => selectedCandidateIds.includes(candidate.document_id));
  const toggleCandidate = (documentId: number) => setSelectedCandidateIds((current) =>
    current.includes(documentId) ? current.filter((id) => id !== documentId) : [...current, documentId]
  );
  const financialContracts = contracts.filter((item) => item.contract_kind !== "prime_reference");
  const contract = financialContracts.find((item) => item.id === selectedContractId);
  const baselineIds = new Set(finance?.baselines.filter((item) => item.contract_id === selectedContractId).map((item) => item.id) || []);
  const scheduleCount = finance?.schedule.filter((item) => baselineIds.has(item.baseline_id)).length || 0;
  const budgetCount = finance?.budget.filter((item) => item.contract_id === selectedContractId).length || 0;
  const cashCount = finance?.cash_flow.filter((item) => item.contract_id === selectedContractId).length || 0;
  const actsCount = finance?.acts.filter((item) => item.contract_id === selectedContractId).length || 0;
  const steps: [string, string, boolean, string][] = [["1", "Договор", Boolean(contract), "contracts"], ["2", "ГПР", scheduleCount > 0, "schedule"], ["3", "Бюджет", budgetCount > 0, "budget"], ["4", "ДДС / счёт", cashCount > 0, "invoice"], ["5", "Акты", actsCount > 0, "act"]];
  const upload = (kind: "budget" | "cash-flow", files: FileList | null, input: HTMLInputElement | null) => {
    if (contract && files?.length) onUploadFinance(Array.from(files), contract.id, kind);
    if (input) input.value = "";
  };
  return <>
    <section className="finance-metrics">{[["Бюджет", money(finance?.summary.budget_planned)], ["Законтрактовано", money(finance?.summary.budget_committed)], ["Факт", money(finance?.summary.budget_actual)], ["Прогноз", money(finance?.summary.budget_forecast)], ["Отклонение", money(finance?.summary.budget_variance)], ["Прогноз остатка", money(finance?.summary.cash_balance_forecast)], ["Кассовый разрыв", money(finance?.summary.cash_gap)]].map(([label, value]) => <article className="card" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    <section className="card finance-contract-chain"><div><span className="eyebrow">ФИНАНСОВЫЙ КОНТУР НАШЕЙ КОМПАНИИ</span><h2>Договор → ГПР → бюджет → ДДС → акты</h2><p>Генподрядный договор остаётся контекстом объекта. Деньги и план работ учитываются только по нашему доходному или расходному договору.</p></div><select aria-label="Финансовый договор" value={selectedContractId} onChange={(event) => onSelectContract(Number(event.target.value))}><option value={0}>Выберите наш финансовый договор</option>{financialContracts.map((item) => <option value={item.id} key={item.id}>{item.number} — {item.title}</option>)}</select></section>
    <section className="card finance-import-hub">
      <div className="finance-import-heading"><span className="eyebrow">БЫСТРЫЙ СТАРТ</span><h2>Загрузить плановые данные</h2><p>Сначала выберите договор выше. Загруженный файл будет разобран, а строки появятся как предложения для проверки — без автоматического утверждения.</p></div>
      <div className="finance-import-actions">
        <article><b>1</b><div><strong>График работ</strong><small>Отдельный полноэкранный раздел для ГПР и диаграммы Ганта.</small></div><button type="button" onClick={onOpenSchedule}>Открыть ГПР</button></article>
        <article><b>2</b><div><strong>Бюджет</strong><small>Excel, CSV, PDF, Word или скан сметы и бюджета.</small></div><input ref={budgetInput} hidden aria-label="Файл бюджета" type="file" multiple accept=".xlsx,.xls,.csv,.pdf,.doc,.docx,.txt,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp" onChange={(event) => upload("budget", event.target.files, event.currentTarget)} /><button type="button" disabled={!contract} onClick={() => budgetInput.current?.click()}>Загрузить бюджет</button></article>
        <article><b>3</b><div><strong>Плановый ДДС</strong><small>Платёжный календарь или план поступлений и выплат.</small></div><input ref={cashFlowInput} hidden aria-label="Файл планового ДДС" type="file" multiple accept=".xlsx,.xls,.csv,.pdf,.doc,.docx,.txt,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp" onChange={(event) => upload("cash-flow", event.target.files, event.currentTarget)} /><button type="button" disabled={!contract} onClick={() => cashFlowInput.current?.click()}>Загрузить плановый ДДС</button></article>
      </div>
      {!contract && <p className="finance-import-hint">Выберите финансовый договор — после этого кнопки загрузки бюджета и ДДС станут доступны.</p>}
    </section>
    <section className="card finance-chain-guide"><div className="card-head"><div><h2>Мастер запуска исполнения</h2><p>{contract ? `Договор ${contract.number}: выполните шаги слева направо.` : "Выберите договор выше — записи не должны терять договорный контекст."}</p></div></div><div className="finance-chain-steps">{steps.map(([number, label, complete, kind]) => <button type="button" className={complete ? "complete" : "pending"} disabled={!contract || kind === "contracts"} onClick={() => kind === "schedule" ? onOpenSchedule() : kind !== "contracts" && onPrepare(kind)} key={label}><b>{complete ? "✓" : number}</b><span>{label}</span><small>{complete ? "готово" : kind === "contracts" ? "выберите договор" : kind === "schedule" ? "открыть" : "добавить"}</small></button>)}</div>{contract && !scheduleCount && <p className="finance-next-action">Следующий шаг: откройте отдельный раздел «График работ» и добавьте этапы ГПР. После этого загрузите бюджет и плановый ДДС.</p>}</section>
    <section className="card finance-document-assistant"><div className="card-head"><div><span className="eyebrow">АНАЛИЗ ПРОЕКТНОЙ ПАПКИ</span><h2>Найденные ГПР, бюджеты, ДДС, счета и акты</h2><p>Система предлагает роль документа по названию и извлечённому тексту. Оригинал не меняется; перед созданием записи проверьте поля.</p></div><div className="finance-document-actions"><button type="button" onClick={onUpload}>Загрузить счёт или акт</button><button type="button" onClick={onReload}>Обновить анализ</button></div></div>
      <div className="finance-candidate-summary"><strong>Всего: {candidates.length}</strong><span>ГПР: {candidateCounts.schedule || 0}</span><span>Бюджеты: {candidateCounts.budget || 0}</span><span>ДДС: {candidateCounts["cash-flow"] || 0}</span><span>Счета: {candidateCounts.invoice || 0}</span><span>Акты: {candidateCounts.act || 0}</span></div>
      <div className="finance-candidate-controls"><select aria-label="Тип финансового документа" value={candidateKind} onChange={(event) => setCandidateKind(event.target.value)}><option value="all">Все типы</option><option value="schedule">ГПР</option><option value="budget">Бюджет / смета</option><option value="cash-flow">ДДС</option><option value="invoice">Счета</option><option value="act">Акты</option></select><select aria-label="Минимальная уверенность" value={minimumScore} onChange={(event) => setMinimumScore(Number(event.target.value))}><option value={20}>От 20%</option><option value={50}>От 50%</option><option value={80}>От 80%</option></select><button type="button" className="secondary" disabled={!visibleCandidates.length} onClick={() => setSelectedCandidateIds(visibleCandidates.map((candidate) => candidate.document_id))}>Выбрать найденные ({visibleCandidates.length})</button><button type="button" disabled={!selectedCandidates.length} onClick={() => onUseCandidate(selectedCandidates[0])}>Открыть первый выбранный ({selectedCandidates.length})</button></div>
      <p className="finance-warning">Отмеченные документы остаются списком для последовательной проверки. Для табличного файла один просмотр создаёт предложения сразу по всем выбранным строкам; факты появятся только после отдельного подтверждения.</p>
      <div className="finance-candidates">{visibleCandidates.map((candidate) => <article className={selectedCandidateIds.includes(candidate.document_id) ? "selected" : ""} key={candidate.document_id}><label><input type="checkbox" checked={selectedCandidateIds.includes(candidate.document_id)} onChange={() => toggleCandidate(candidate.document_id)} /> В пакет</label><div><span>{({ schedule: "ГПР", budget: "Бюджет / смета", invoice: "Счёт", "cash-flow": "ДДС", act: "Акт" } as Record<string, string>)[candidate.kind]}</span><b>{candidate.score}%</b></div><strong title={candidate.name}>{candidate.name}</strong><small>{candidate.reasons.join("; ") || "совпадение по структуре документа"}</small><small>{[candidate.hints.amount ? money(Number(candidate.hints.amount)) : "", candidate.hints.date || "", candidate.hints.number ? `№ ${candidate.hints.number}` : ""].filter(Boolean).join(" · ")}</small><button type="button" onClick={() => onUseCandidate(candidate)}>Проверить и использовать</button></article>)}{!visibleCandidates.length && <p className="finance-empty">По выбранному фильтру документов нет. Измените тип или порог уверенности.</p>}</div></section>
  </>;
}
