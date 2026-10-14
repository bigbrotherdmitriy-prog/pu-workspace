import type { ComponentProps, ReactNode } from "react";
import { Trash2 } from "lucide-react";
import { lazy } from "../../utils/lazyRoute";
import { ContractDetails, ContractTermsFields } from "./ContractDetails";
import { contractMoney, inheritedVatLabel } from "./contractCommercial";
import type { ContractBudgetDraft, ContractEditDraft, ContractRow } from "./types";
import type { ContractDetailsState } from "./useContractDetailsState";
import type { CostCategory, FinanceOverview } from "../finance/types";

const ContractDocumentPicker = lazy(() => import("./ContractDocumentPicker").then((m) => ({ default: m.ContractDocumentPicker })));

type Props = {
  item: ContractRow;
  packageContent?: ReactNode;
  state: ContractDetailsState;
  contracts: ContractRow[];
  projects: { id: number; currency?: string }[];
  projectId: number;
  finance: FinanceOverview | null;
  costCategories: CostCategory[];
  documentRows: ComponentProps<typeof ContractDocumentPicker>["documents"];
  currentUser: { id: number; is_admin: boolean } | null;
  members: { user_id: number; role: string }[];
  setNotice: (message: string) => void;
};

/** One contract card on «Договоры»: tree position, budget proposal, editing, source document, analysis and the ГПР–бюджет–ДДС chain (moved from App.tsx). */
export function ContractDetailsPanel({ item, packageContent, state, contracts, projects, projectId, finance, costCategories, documentRows, currentUser, members, setNotice }: Props) {
  const {
    contractDocumentTabs, setContractDocumentTabs, contractDocumentQueries, setContractDocumentQueries, contractCatalogOpen, setContractCatalogOpen, contractStructureDrafts, setContractStructureDrafts, contractEditDrafts, setContractEditDrafts, contractEditFeedback, contractEditSaving, contractBudgetDrafts, setContractBudgetDrafts, contractSourceCandidates, contractFinancialChecks, contractCandidateBusy, linkContractDocument, linkContractParent, suggestContractDocuments, analyzeContract, beginContractEdit, saveContractEdit, compareContractEdit, proposeContractBudget, confirmContractBudget, rejectContractBudget, deleteContract, archiveContract, openContractControl,
  } = state;
  const currency = projects.find((project) => project.id === projectId)?.currency || "RUB";
  const currencyLabel = currency === "RUB" ? "₽" : currency;
  return (
  <ContractDetails contract={item} currency={currency}>
      <div>
        <span className={`contract-status ${item.status}`}>
          {item.contract_kind === "prime_reference" ? "ГЕНПОДРЯД · КОНТЕКСТ" : item.contract_kind === "revenue_subcontract" ? "НАШ СУБПОДРЯД · ДОХОД" : item.contract_kind === "downstream_subcontract" ? "НАШ ИСПОЛНИТЕЛЬ · РАСХОД" : item.contract_kind === "supply" ? "ПОСТАВКА · РАСХОД" : "ПРЯМОЙ ДОГОВОР · ДОХОД"} · {item.status}
        </span>
        <span className={`contract-source-badge ${item.source_document_id ? "linked" : "missing"}`}>
          {item.source_document_id ? "✓ Договор привязан к документу" : "! Документ договора не привязан"}
        </span>
        {item.parent_contract_id && <small>
          Связан с договором {contracts.find((parent) => parent.id === item.parent_contract_id)?.number || `№${item.parent_contract_id}`}
        </small>}
        {(() => {
          const draft = contractStructureDrafts[item.id] || { kind: item.contract_kind || "customer", parentId: item.parent_contract_id || 0 };
          const needsParent = !["prime_reference", "customer"].includes(draft.kind);
          return <div className="contract-parent-link">
            <span>Место в дереве</span>
            <select value={draft.kind} onChange={(event) => setContractStructureDrafts((current) => ({ ...current, [item.id]: { kind: event.target.value, parentId: 0 } }))}>
              <option value="prime_reference">Генподрядный договор — корень</option>
              <option value="customer">Прямой договор — корень</option>
              <option value="revenue_subcontract">Наш договор под генподрядным</option>
              <option value="downstream_subcontract">Субподрядчик / субсубподрядчик</option>
              <option value="supply">Поставщик</option>
            </select>
            {needsParent && <select value={draft.parentId} onChange={(event) => setContractStructureDrafts((current) => ({ ...current, [item.id]: { ...draft, parentId: Number(event.target.value) } }))}>
              <option value={0}>Выберите непосредственный вышестоящий договор</option>
              {contracts.filter((candidate) => {
                if (candidate.id === item.id) return false;
                if (draft.kind === "revenue_subcontract") return ["prime_reference", "customer"].includes(candidate.contract_kind || "customer");
                return ["prime_reference", "customer", "revenue_subcontract", "downstream_subcontract"].includes(candidate.contract_kind || "customer");
              }).map((candidate) => <option value={candidate.id} key={candidate.id}>{candidate.number} — {candidate.counterparty || candidate.title}</option>)}
            </select>}
            <button className="secondary" disabled={needsParent && !draft.parentId} onClick={() => void linkContractParent(item.id, draft.kind, draft.parentId)}>Сохранить связь</button>
          </div>;
        })()}
        {item.contract_kind !== "prime_reference" && <div className="contract-budget-proposal">
          <button className="secondary" disabled={item.amount == null || Number(item.amount) <= 0} onClick={() => void proposeContractBudget(item.id)}>
            Предложить бюджет по договору
          </button>
          {(item.amount == null || Number(item.amount) <= 0) && <small>Сначала укажите общую сумму договора.</small>}
          {(item.budget_proposals || []).filter((proposal) => proposal.status === "proposed").slice(0, 1).map((proposal) => {
            const draft = contractBudgetDrafts[proposal.id] || {
              amount: String(proposal.amount), description: proposal.description,
              categoryId: Number(proposal.selected_cost_category_id || 0),
            };
            const canManage = Boolean(currentUser?.is_admin || members.some((member) =>
              member.user_id === currentUser?.id && ["manager", "owner"].includes(member.role),
            ));
            const change = (values: Partial<ContractBudgetDraft>) => setContractBudgetDrafts((current) => ({
              ...current, [proposal.id]: { ...draft, ...values },
            }));
            return <div className="contract-edit-form" key={proposal.id}>
              <strong>{proposal.operation === "revise" ? "Пересмотр строки бюджета" : "Предложение строки бюджета"}</strong>
              <small>Общая сумма — одна строка. Аванс {proposal.advance_amount != null ? contractMoney(proposal.advance_amount, proposal.currency || currency) : "не указан"}; удержание {proposal.retention_percent != null ? `${proposal.retention_percent}%` : "не указано"} — справочно.</small>
              <small>{inheritedVatLabel(proposal.vat_snapshot)}</small>
              <input aria-label="Сумма предложения бюджета" type="number" min="0.01" step="0.01" value={draft.amount} onChange={(event) => change({ amount: event.target.value })} />
              <input aria-label="Описание предложения бюджета" value={draft.description} onChange={(event) => change({ description: event.target.value })} />
              <select aria-label="Категория предложения бюджета" value={draft.categoryId || ""} onChange={(event) => change({ categoryId: Number(event.target.value) })}>
                <option value="">Выберите категорию затрат</option>
                {costCategories.filter((category) => category.is_active).map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
              </select>
              {proposal.source_document_id && <small>Источник закреплён: документ #{proposal.source_document_id}, версия #{proposal.source_document_version_id}.</small>}
              {canManage ? <div className="contract-edit-actions">
                <button className="secondary" onClick={() => void rejectContractBudget(proposal.id)}>Отклонить</button>
                <button disabled={!draft.amount || !draft.description.trim() || !draft.categoryId} onClick={() => void confirmContractBudget(proposal)}>Подтвердить бюджет</button>
              </div> : <small>Предложение создано. Подтвердить или отклонить его может менеджер проекта.</small>}
            </div>;
          })}
        </div>}
        {contractEditDrafts[item.id] ? (() => {
          const draft = contractEditDrafts[item.id];
          const feedback = contractEditFeedback[item.id];
          const saving = contractEditSaving[item.id];
          const change = (field: Exclude<keyof ContractEditDraft, "expectedRecordVersion">, value: string) => setContractEditDrafts((current) => ({
            ...current, [item.id]: { ...current[item.id], [field]: value },
          }));
          return <div className="contract-edit-form" role="form" aria-label={`Редактирование договора ${item.number}`}>
            <strong>Редактирование договора</strong>
            <small>Версия открытого черновика: {draft.expectedRecordVersion}</small>
            {feedback?.message && <div className="form-operation-error" role="alert">
              {feedback.message}
              {feedback.conflict && <p>Договор изменился после открытия редактора. Введённые значения сохранены. Автоматической перезаписи нет: загрузите текущую карточку и сравните изменения.</p>}
            </div>}
            {feedback?.conflict && <button type="button" className="secondary" disabled={feedback.comparing} onClick={() => void compareContractEdit(item.id)}>
              {feedback.comparing ? "Загружаю текущую карточку…" : "Загрузить текущую карточку для сравнения"}
            </button>}
            {feedback?.latest && <section className="contract-edit-comparison" role="region" aria-label="Сравнение версий договора">
              <strong>Черновик v{draft.expectedRecordVersion} / текущая карточка v{feedback.latest.record_version}</strong>
              <table><thead><tr><th>Поле</th><th>Ваш черновик</th><th>Текущая карточка</th></tr></thead><tbody>
                {([
                  ["Номер", draft.number, feedback.latest.number], ["Название", draft.title, feedback.latest.title],
                  ["Контрагент", draft.counterparty, feedback.latest.counterparty], ["Сумма", draft.amount, feedback.latest.amount],
                  ["Аванс", draft.advanceAmount, feedback.latest.advance_amount], ["Удержание, %", draft.retentionPercent, feedback.latest.retention_percent],
                  ["Дата подписания", draft.signedAt, feedback.latest.signed_at?.slice(0, 10)],
                  ["НДС", draft.vatMode, feedback.latest.vat_mode], ["Ставка НДС, %", draft.vatRate, feedback.latest.vat_rate],
                  ["Исполнение с", draft.performedFrom, feedback.latest.performed_from], ["Исполнение до", draft.performedTo, feedback.latest.performed_to],
                  ["Гарантия до", draft.warrantyUntil, feedback.latest.warranty_until], ["Статус", draft.status, feedback.latest.status],
                ] as const).map(([label, edited, latest]) => <tr key={label}><th>{label}</th><td>{edited === "" ? "—" : edited}</td><td>{latest ?? "—"}</td></tr>)}
              </tbody></table>
              <button type="button" className="secondary" onClick={() => {
                if (window.confirm("Заменить введённый черновик текущей карточкой? Несохранённые значения будут потеряны.")) beginContractEdit(feedback.latest!);
              }}>Начать заново с текущей версии</button>
            </section>}
            <input disabled={saving} aria-invalid={Boolean(feedback?.fieldErrors?.number)} value={draft.number} onChange={(event) => change("number", event.target.value)} aria-label="Номер редактируемого договора" placeholder="Номер договора" />
            <input disabled={saving} aria-invalid={Boolean(feedback?.fieldErrors?.title)} value={draft.title} onChange={(event) => change("title", event.target.value)} aria-label="Название редактируемого договора" placeholder="Название" />
            <input disabled={saving} aria-invalid={Boolean(feedback?.fieldErrors?.counterparty)} value={draft.counterparty} onChange={(event) => change("counterparty", event.target.value)} aria-label="Контрагент редактируемого договора" placeholder="Контрагент" />
            <input disabled={saving} aria-invalid={Boolean(feedback?.fieldErrors?.amount)} type="number" min="0" step="0.01" value={draft.amount} onChange={(event) => change("amount", event.target.value)} aria-label={`Сумма редактируемого договора с НДС, ${currencyLabel}`} placeholder={`Сумма договора, ${currencyLabel}`} />
            <input disabled={saving} aria-invalid={Boolean(feedback?.fieldErrors?.advance_amount)} type="number" min="0" step="0.01" value={draft.advanceAmount} onChange={(event) => change("advanceAmount", event.target.value)} aria-label={`Аванс редактируемого договора с НДС, ${currencyLabel}`} placeholder={`Аванс, ${currencyLabel}`} />
            <input disabled={saving} aria-invalid={Boolean(feedback?.fieldErrors?.retention_percent)} type="number" min="0" max="100" step="0.01" value={draft.retentionPercent} onChange={(event) => change("retentionPercent", event.target.value)} aria-label="Удержание редактируемого договора, %" placeholder="Удержание, %" />
            <label>Дата подписания<input aria-label="Дата подписания редактируемого договора" aria-invalid={Boolean(feedback?.fieldErrors?.signed_at)} disabled={saving} type="date" value={draft.signedAt} onChange={(event) => change("signedAt", event.target.value)} /></label>
            <ContractTermsFields draft={draft} disabled={saving} onChange={change} fieldErrors={feedback?.fieldErrors} />
            <select aria-label="Статус редактируемого договора" disabled={saving} value={draft.status} onChange={(event) => change("status", event.target.value)}>
              <option value="draft">Черновик</option><option value="active">Действует</option>
              <option value="completed">Завершён</option><option value="terminated">Расторгнут</option><option value="archived">В архиве</option>
            </select>
            <div className="contract-edit-actions">
              <button className="secondary" disabled={saving} onClick={() => setContractEditDrafts((current) => { const next = { ...current }; delete next[item.id]; return next; })}>Отмена</button>
              <button disabled={saving || Boolean(feedback?.conflict) || !draft.number.trim() || !draft.title.trim()} onClick={() => void saveContractEdit(item.id)}>{saving ? "Сохраняю…" : "Сохранить изменения"}</button>
            </div>
          </div>;
        })() : <div className="contract-record-actions">
          <button className="secondary" onClick={() => beginContractEdit(item)}>Редактировать</button>
          {item.status !== "archived" && <button className="secondary" onClick={() => void archiveContract(item)}>Архивировать</button>}
          <button className="danger" onClick={() => void deleteContract(item)}><Trash2 /> Удалить договор</button>
        </div>}
      </div>
      <div className="contract-links">
        <ContractDocumentPicker
          contractId={item.id}
          sourceDocumentId={item.source_document_id}
          open={Boolean(contractCatalogOpen[item.id])}
          busy={contractCandidateBusy === item.id}
          tab={contractDocumentTabs[item.id] || "recommended"}
          query={contractDocumentQueries[item.id] || ""}
          documents={documentRows}
          candidates={contractSourceCandidates[item.id] || []}
          onOpen={() => {
            setContractCatalogOpen((current) => ({ ...current, [item.id]: true }));
            setContractDocumentTabs((current) => ({ ...current, [item.id]: "server" }));
          }}
          onClose={() => setContractCatalogOpen((current) => ({ ...current, [item.id]: false }))}
          onSuggest={() => void suggestContractDocuments(item.id)}
          onTabChange={(tab) => setContractDocumentTabs((current) => ({ ...current, [item.id]: tab }))}
          onQueryChange={(value) => setContractDocumentQueries((current) => ({ ...current, [item.id]: value }))}
          onLink={async (documentId) => {
            if (await linkContractDocument(item.id, documentId)) {
              setContractCatalogOpen((current) => ({ ...current, [item.id]: false }));
              setNotice("Договор привязан. Теперь нажмите «Проанализировать договор».");
            }
          }}
        />
        <button
          className="secondary"
          onClick={() => analyzeContract(item.id)}
        >
          2. Автоматически прикрепить, прочитать и проверить условия
        </button>
        {item.source_document_id && !item.analysis?.source_ready && (
          <small>Сначала дождитесь завершения анализа рабочей папки: текст договора ещё не извлечён.</small>
        )}
        {item.analysis && (item.analysis.tasks > 0 || item.analysis.obligations > 0) && (
          <div className="contract-analysis-status">
            <span>Задачи <b>{item.analysis.tasks}</b></span>
            <span>Обязательства <b>{item.analysis.obligations}</b></span>
            <span>Риски <b>{item.analysis.risks}</b></span>
            <span>Решения <b>{item.analysis.decisions}</b></span>
          </div>
        )}
        {contractFinancialChecks[item.id] && (
          <div className={`contract-analysis-status ${contractFinancialChecks[item.id].mismatches.length ? "warning" : ""}`} role="status">
            <span>Источник <b>{contractFinancialChecks[item.id].sourceName || "договор"}</b></span>
            <span>Сумма <b>{item.amount != null ? contractMoney(item.amount, currency) : "не найдена"}</b></span>
            <span>Аванс <b>{item.advance_amount != null ? contractMoney(item.advance_amount, currency) : "не найден"}</b></span>
            <span>Удержание <b>{item.retention_percent != null ? `${item.retention_percent}%` : "не найдено"}</b></span>
            <span>Сверка <b>{contractFinancialChecks[item.id].mismatches.length ? `расхождений: ${contractFinancialChecks[item.id].mismatches.length}` : "совпадает"}</b></span>
            {contractFinancialChecks[item.id].mismatches.map((mismatch) => <small key={mismatch.field}>
              {mismatch.label}: в карточке {mismatch.current}, в договоре {mismatch.extracted}. {mismatch.evidence || "Проверьте исходный пункт договора."}
            </small>)}
          </div>
        )}
        <div className="contract-chain-status">
          <span>3. ГПР <b>{finance?.baselines.filter((row) => row.contract_id === item.id).length ? "создан" : "не создан"}</b></span>
          <span>4. Бюджет <b>{finance?.budget.filter((row) => row.contract_id === item.id).length || 0} строк</b></span>
          <span>5. ДДС <b>{finance?.cash_flow.filter((row) => row.contract_id === item.id).length || 0} записей</b></span>
        </div>
        <button onClick={() => openContractControl(item.id)}>
          {finance?.baselines.some((row) => row.contract_id === item.id)
            ? "3. Открыть ГПР, бюджет и ДДС"
            : "3. Создать ГПР, бюджет и ДДС"}
        </button>
      </div>
    {packageContent}
  </ContractDetails>
  );
}
