import { useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { api, ApiError } from "../../api/client";
import { contractFieldErrors, contractFieldErrorMessage } from "./contractCommercial";
import { requestContractDeletionConfirmation } from "./contractDeletion";
import type { GprDdsTab } from "../finance/GprDdsWorkspace";
import type {
  ContractBudgetDraft, ContractBudgetProposal, ContractEditDraft, ContractEditFeedback,
  ContractFinancialCheck, ContractRow, ContractSourceCandidate,
} from "./types";

type Options = {
  projectId: number;
  projectIdRef: MutableRefObject<number>;
  contracts: ContractRow[];
  setContracts: Dispatch<SetStateAction<ContractRow[]>>;
  load: () => Promise<void>;
  loadFinance: () => Promise<void>;
  setNotice: (message: string) => void;
  setError: (message: string) => void;
  setSelectedFinanceContractId: (contractId: number) => void;
  setGprDdsTab: (tab: GprDdsTab) => void;
  setActive: (section: string) => void;
};

/**
 * State and actions of the contract card on «Договоры»: tree position, budget
 * proposal, editing with version conflicts, source document and analysis.
 * Moved from App.tsx without behaviour changes. `contractProjectEpochRef`
 * guards in-flight requests across project switches and is also read by the
 * contract upload actions that still live in App.
 */
export function useContractDetailsState({
  projectId, projectIdRef, contracts, setContracts, load, loadFinance,
  setNotice, setError, setSelectedFinanceContractId, setGprDdsTab, setActive,
}: Options) {
  const [contractDocumentTabs, setContractDocumentTabs] = useState<Record<number, "recommended" | "server" | "upload" | "google">>({});
  const [contractDocumentQueries, setContractDocumentQueries] = useState<Record<number, string>>({});
  const [contractCatalogOpen, setContractCatalogOpen] = useState<Record<number, boolean>>({});
  const [contractStructureDrafts, setContractStructureDrafts] = useState<Record<number, { kind: string; parentId: number }>>({});
  const [contractEditDrafts, setContractEditDrafts] = useState<Record<number, ContractEditDraft>>({});
  const [contractEditFeedback, setContractEditFeedback] = useState<Record<number, ContractEditFeedback>>({});
  const [contractEditSaving, setContractEditSaving] = useState<Record<number, boolean>>({});
  const [contractBudgetDrafts, setContractBudgetDrafts] = useState<Record<number, ContractBudgetDraft>>({});
  const [contractSourceCandidates, setContractSourceCandidates] = useState<Record<number, ContractSourceCandidate[]>>({});
  const [contractFinancialChecks, setContractFinancialChecks] = useState<Record<number, ContractFinancialCheck>>({});
  const [contractCandidateBusy, setContractCandidateBusy] = useState(0);
  const contractProjectEpochRef = useRef(0);
  const contractSaveRequestsRef = useRef(new Set<number>());

  async function linkContractDocument(contractId: number, documentId: number) {
    try {
      const expected = contracts.find((item) => item.id === contractId)?.record_version;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      await api(`/projects/${projectId}/contracts/${contractId}`, {
        method: "PATCH",
        body: JSON.stringify({ expected_record_version: expected, source_document_id: documentId || null }),
      });
      setNotice(documentId ? "Документ-источник привязан к договору" : "Связь с документом снята");
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }
  async function linkContractParent(contractId: number, contractKind: string, parentContractId: number) {
    try {
      const expected = contracts.find((item) => item.id === contractId)?.record_version;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      await api(`/projects/${projectId}/contracts/${contractId}`, {
        method: "PATCH",
        body: JSON.stringify({ expected_record_version: expected, contract_kind: contractKind, parent_contract_id: parentContractId || null }),
      });
      setNotice("Вышестоящий договор сохранён — дерево перестроено");
      setContractStructureDrafts((current) => { const next = { ...current }; delete next[contractId]; return next; });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function suggestContractDocuments(contractId: number) {
    try {
      setError("");
      setContractCandidateBusy(contractId);
      const result = await api(`/projects/${projectId}/contracts/${contractId}/source-candidates`);
      setContractSourceCandidates((current) => ({ ...current, [contractId]: result.candidates || [] }));
      setContractDocumentTabs((current) => ({ ...current, [contractId]: "recommended" }));
      setNotice(result.recommended_document_id
        ? "Вероятный документ договора найден. Проверьте объяснение и подтвердите привязку."
        : "Точного совпадения не найдено. Используйте поиск или сначала завершите анализ документов.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setContractCandidateBusy(0);
    }
  }
  async function analyzeContract(contractId: number) {
    try {
      setError("");
      const result = await api(`/projects/${projectId}/contracts/${contractId}/analyze`, {
        method: "POST",
      });
      const financial = result.financial_check;
      setContractFinancialChecks((current) => ({ ...current, [contractId]: { ...financial, sourceName: result.source?.name } }));
      const linked = result.source?.automatically_linked ? ` Автоматически прикреплён файл «${result.source.name}».` : "";
      const applied = financial?.applied?.length ? ` Заполнены условия: ${financial.applied.join(", ")}.` : "";
      const mismatches = financial?.mismatches?.length ? ` ВНИМАНИЕ: расхождений в финансовых условиях — ${financial.mismatches.length}; проверьте карточку договора.` : " Финансовые расхождения не обнаружены.";
      setNotice(`Договор проанализирован.${linked}${applied}${mismatches} Задач ${result.analysis.tasks}, обязательств ${result.analysis.obligations}, строк графика платежей ${result.created?.payment_schedule || 0}.`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function beginContractEdit(item: ContractRow) {
    setContractEditFeedback((current) => ({ ...current, [item.id]: { message: "" } }));
    setContractEditDrafts((current) => ({ ...current, [item.id]: {
      number: item.number, title: item.title, counterparty: item.counterparty || "",
      amount: item.amount?.toString() || "", advanceAmount: item.advance_amount?.toString() || "",
      retentionPercent: item.retention_percent?.toString() || "", signedAt: item.signed_at?.slice(0, 10) || "",
      vatMode: item.vat_mode || "unspecified", vatRate: item.vat_rate ?? "",
      performedFrom: item.performed_from?.slice(0, 10) || "", performedTo: item.performed_to?.slice(0, 10) || "",
      warrantyUntil: item.warranty_until?.slice(0, 10) || "",
      status: item.status, expectedRecordVersion: item.record_version,
    } }));
  }
  async function saveContractEdit(contractId: number) {
    const draft = contractEditDrafts[contractId];
    if (!draft?.number.trim() || !draft.title.trim() || contractSaveRequestsRef.current.has(contractId) || contractEditFeedback[contractId]?.conflict) return;
    const requestedProjectId = projectId;
    const epoch = contractProjectEpochRef.current;
    const current = () => projectIdRef.current === requestedProjectId && contractProjectEpochRef.current === epoch;
    contractSaveRequestsRef.current.add(contractId);
    setContractEditSaving((rows) => ({ ...rows, [contractId]: true }));
    setContractEditFeedback((rows) => ({ ...rows, [contractId]: { message: "" } }));
    try {
      setError("");
      const expected = draft.expectedRecordVersion;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      const saved = await api<ContractRow>(`/projects/${projectId}/contracts/${contractId}`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_record_version: expected,
          number: draft.number.trim(), title: draft.title.trim(), counterparty: draft.counterparty.trim() || null,
          amount: draft.amount || null,
          advance_amount: draft.advanceAmount || null,
          retention_percent: draft.retentionPercent || null,
          signed_at: draft.signedAt || null, status: draft.status,
          vat_mode: draft.vatMode, vat_rate: draft.vatMode === "rate" ? draft.vatRate || null : null,
          performed_from: draft.performedFrom || null, performed_to: draft.performedTo || null,
          warranty_until: draft.warrantyUntil || null,
        }),
      });
      if (!current()) return;
      setContractEditDrafts((current) => { const next = { ...current }; delete next[contractId]; return next; });
      setNotice(["Договор обновлён. Связанные документы, ГПР, бюджет и ДДС сохранены", ...(saved.warnings || []).map((warning) => warning.message)].join(". "));
      await load();
    } catch (e) {
      if (!current()) return;
      const fieldErrors = e instanceof ApiError ? contractFieldErrors(e.details) : {};
      const message = [contractFieldErrorMessage(fieldErrors), e instanceof Error ? e.message : "Не удалось сохранить договор."].filter(Boolean).join(". ");
      const conflict = e instanceof ApiError && e.status === 409;
      setContractEditFeedback((rows) => ({ ...rows, [contractId]: { message, conflict, fieldErrors } }));
      setError(message);
    } finally {
      if (current()) {
        contractSaveRequestsRef.current.delete(contractId);
        setContractEditSaving((rows) => ({ ...rows, [contractId]: false }));
      }
    }
  }
  async function compareContractEdit(contractId: number) {
    const requestedProjectId = projectId;
    const epoch = contractProjectEpochRef.current;
    const current = () => projectIdRef.current === requestedProjectId && contractProjectEpochRef.current === epoch;
    setContractEditFeedback((rows) => ({ ...rows, [contractId]: { ...rows[contractId], comparing: true } }));
    try {
      const response = await api<{ contracts: ContractRow[] }>(`/projects/${requestedProjectId}/contracts`);
      if (!current()) return;
      const latest = response.contracts.find((row) => row.id === contractId);
      if (!latest) throw new Error("Договор отсутствует в текущем списке. Черновик сохранён.");
      setContracts((rows) => rows.map((row) => row.id === contractId && row.record_version <= latest.record_version ? latest : row));
      setContractEditFeedback((rows) => ({ ...rows, [contractId]: { ...rows[contractId], latest, comparing: false } }));
    } catch (e) {
      if (current()) setContractEditFeedback((rows) => ({ ...rows, [contractId]: {
        ...rows[contractId], message: e instanceof Error ? e.message : "Не удалось загрузить сравнение.", comparing: false,
      } }));
    }
  }
  async function proposeContractBudget(contractId: number) {
    try {
      setError("");
      const proposal = await api(`/projects/${projectId}/contracts/${contractId}/budget-proposals`, { method: "POST" });
      setContractBudgetDrafts((current) => ({ ...current, [proposal.id]: {
        amount: String(proposal.amount), description: proposal.description,
        categoryId: Number(proposal.selected_cost_category_id || 0),
      } }));
      setNotice(proposal.operation === "revise"
        ? "Подготовлено предложение пересмотреть существующую строку бюджета. Проверьте и подтвердите как менеджер."
        : "Подготовлено предложение одной строки бюджета на общую сумму договора. Проверьте и подтвердите как менеджер.");
      await load();
    } catch (e) { setError((e as Error).message); }
  }
  async function confirmContractBudget(proposal: ContractBudgetProposal) {
    const draft = contractBudgetDrafts[proposal.id] || {
      amount: String(proposal.amount), description: proposal.description,
      categoryId: Number(proposal.selected_cost_category_id || 0),
    };
    if (!draft.categoryId) { setError("Выберите категорию затрат перед подтверждением"); return; }
    try {
      setError("");
      await api(`/contract-budget-proposals/${proposal.id}`, {
        method: "PATCH", body: JSON.stringify({
          amount: draft.amount, description: draft.description,
          selected_cost_category_id: draft.categoryId,
        }),
      });
      await api(`/contract-budget-proposals/${proposal.id}/confirm`, { method: "POST" });
      setNotice(proposal.operation === "revise"
        ? "Строка бюджета пересмотрена без задвоения суммы; факт и обязательства сохранены."
        : "Предложение подтверждено: создана одна строка бюджета.");
      await load(); await loadFinance();
    } catch (e) { setError((e as Error).message); }
  }
  async function rejectContractBudget(proposalId: number) {
    try {
      setError("");
      await api(`/contract-budget-proposals/${proposalId}/reject`, { method: "POST" });
      setNotice("Предложение бюджета отклонено. Финансовые записи не создавались.");
      await load();
    } catch (e) { setError((e as Error).message); }
  }
  async function deleteContract(item: ContractRow) {
    try {
      const preview = await api(`/projects/${projectId}/contracts/${item.id}/deletion-preview`);
      if (preview?.can_delete === false) {
        const count = Object.values(preview.dependencies || {}).reduce((sum: number, value) => sum + Number(value || 0), 0);
        if (window.confirm(`У договора ${count} связанных объектов. Физическое удаление заблокировано. Архивировать договор с сохранением всех связей?`)) {
          await archiveContract(item);
        }
        return;
      }
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    const confirmation = requestContractDeletionConfirmation(item.number);
    if (confirmation === null) return;
    try {
      setError("");
      await api(`/projects/${projectId}/contracts/${item.id}`, {
        method: "DELETE", body: JSON.stringify({ confirmation, expected_record_version: item.record_version }),
      });
      setNotice(`Договор «${item.number}» удалён. Исходные документы сохранены.`);
      await load();
    } catch (e) {
      const message = (e as Error).message;
      setError(message);
      window.alert(message);
    }
  }
  async function archiveContract(item: ContractRow) {
    try {
      setError("");
      await api(`/projects/${projectId}/contracts/${item.id}`, {
        method: "PATCH", body: JSON.stringify({ expected_record_version: item.record_version, status: "archived" }),
      });
      setNotice(`Договор «${item.number}» архивирован. Все документы, ГПР, ДДС и другие связи сохранены.`);
      await load();
    } catch (e) { setError((e as Error).message); }
  }
  async function openContractControl(contractId: number) {
    try {
      setError("");
      const result = await api(`/projects/${projectId}/contracts/${contractId}/initialize-control`, {
        method: "POST",
      });
      setSelectedFinanceContractId(contractId);
      setGprDdsTab("dds");
      setActive("ГПР и ДДС");
      setNotice(result.created
        ? "Цепочка договора создана: заполните этапы ГПР, затем бюджет и ДДС"
        : "Цепочка договора открыта: ГПР, бюджет и ДДС связаны с договором");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  /** Called by the shell when another project is selected, before its data loads. */
  function resetForProjectChange() {
    ++contractProjectEpochRef.current;
    contractSaveRequestsRef.current.clear();
    setContractEditDrafts({});
    setContractEditFeedback({});
    setContractEditSaving({});
    setContractStructureDrafts({});
    setContractBudgetDrafts({});
    setContractCatalogOpen({});
    setContractFinancialChecks({});
  }

  return {
    contractDocumentTabs, setContractDocumentTabs, contractDocumentQueries, setContractDocumentQueries, contractCatalogOpen, setContractCatalogOpen, contractStructureDrafts, setContractStructureDrafts, contractEditDrafts, setContractEditDrafts, contractEditFeedback, setContractEditFeedback, contractEditSaving, setContractEditSaving, contractBudgetDrafts, setContractBudgetDrafts, contractSourceCandidates, setContractSourceCandidates, contractFinancialChecks, setContractFinancialChecks, contractCandidateBusy, setContractCandidateBusy,
    contractProjectEpochRef,
    linkContractDocument, linkContractParent, suggestContractDocuments, analyzeContract, beginContractEdit, saveContractEdit, compareContractEdit, proposeContractBudget, confirmContractBudget, rejectContractBudget, deleteContract, archiveContract, openContractControl,
    resetForProjectChange,
  };
}

export type ContractDetailsState = ReturnType<typeof useContractDetailsState>;
