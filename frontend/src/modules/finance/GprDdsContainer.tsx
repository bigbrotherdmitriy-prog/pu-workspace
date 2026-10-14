import type { ComponentProps } from "react";
import { lazy } from "../../utils/lazyRoute";
import type { GprDdsState } from "./useGprDdsState";

const FinanceModule = lazy(() => import("./FinanceModule").then((m) => ({ default: m.FinanceModule })));
const DdsWorkspace = lazy(() => import("./DdsWorkspace").then((m) => ({ default: m.DdsWorkspace })));
const GprWorkspace = lazy(() => import("./GprWorkspace").then((m) => ({ default: m.GprWorkspace })));
const GprDdsWorkspace = lazy(() => import("./GprDdsWorkspace").then((m) => ({ default: m.GprDdsWorkspace })));
const FinanceOperations = lazy(() => import("./FinanceOperations").then((m) => ({ default: m.FinanceOperations })));

type Props = {
  state: GprDdsState;
  /** Shell section; "График работ" and "Исполнение и финансы" open this screen on a fixed tab. */
  active: string;
  setActive: (section: string) => void;
  collapsed: boolean;
  projectId: number;
  contracts: ComponentProps<typeof GprWorkspace>["contracts"];
  /** Opens the shell's mobile upload sheet for an invoice or act. */
  onUploadFinanceDocument: () => void;
  uploadContractFinance: (files: File[], contractId: number, kind: "schedule" | "budget" | "cash-flow") => Promise<void>;
};

/** The ГПР и ДДС screen: schedule and cash-flow workspaces with their editors, moved out of App. */
export function GprDdsContainer({ state, active, setActive, collapsed, projectId, contracts, onUploadFinanceDocument, uploadContractFinance }: Props) {
  const {
    finance, financeCandidates, financeStructuredPreview, financeStructuredRows, costCategories, invoiceExtractionProposal, invoiceConfirmationError, invoiceConfirming, invoiceAiRetrying,
    selectedFinanceContractId, financeKind, financeTitle, financeAmount, financeDate, financeDirection,
    financeExtra, financeObject, financeCategory, financeNote, financeSourceDocumentId, financeScheduleItemId, financeBudgetLineId, financeBaselineId,
    setFinanceStructuredPreview, setFinanceStructuredRows, setSelectedFinanceContractId,
    setFinanceKind, setFinanceTitle, setFinanceAmount, setFinanceDate, setFinanceDirection, setFinanceExtra, setFinanceObject, setFinanceCategory, setFinanceNote,
    setFinanceScheduleItemId, setFinanceBudgetLineId, setFinanceBaselineId,
    setInvoiceExtractionProposal, editInvoiceExtraction,
    loadFinance, prepareFinanceItem, useFinanceCandidate, reviewUploadedFinanceDocuments,
    importStructuredFinance, editStructuredFinanceRow,
    addFinanceItem, addCostCategory, confirmInvoiceExtraction, rejectInvoiceExtraction, retryInvoiceAiAnalysis,
    confirmFinance, confirmFinanceMany, confirmCashPayment, reverseCashPayment, linkCashFlowControls, linkApprovedCashFlowBudgetLine, mutateCashFlowPlan, undoCashFlowPlanMutation, refreshFinanceVat,
    updateScheduleTask, bulkUpdateSchedule, cloneScheduleBaseline,
    gprDdsTab, setGprDdsTab, financeEditorOpen, setFinanceEditorOpen, focusedScheduleItemId, setFocusedScheduleItemId, uploadDdsInvoices,
  } = state;
  return (
    <section className={`module-overlay gpr-dds-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page gpr-dds-page">
      <GprDdsWorkspace
        tab={active === "Исполнение и финансы" ? "dds" : active === "График работ" ? "gpr" : gprDdsTab}
        onTabChange={(tab) => { setGprDdsTab(tab); if (active !== "ГПР и ДДС") setActive("ГПР и ДДС"); }}
        onClose={() => setActive("Обзор")}
        gpr={<>
        <GprWorkspace
          projectId={projectId}
          finance={finance}
          contracts={contracts}
          selectedContractId={selectedFinanceContractId}
          onSelectContract={setSelectedFinanceContractId}
          onPrepare={(kind, baselineId) => { prepareFinanceItem(kind, baselineId); setFinanceEditorOpen(true); }}
          onUpdateTask={updateScheduleTask}
          onBulkUpdate={bulkUpdateSchedule}
          onCloneBaseline={cloneScheduleBaseline}
          onImported={loadFinance}
          focusTaskId={focusedScheduleItemId}
          onOpenCashFlow={(scheduleItemId) => { setFocusedScheduleItemId(scheduleItemId); setGprDdsTab("dds"); }}
        />
        {financeEditorOpen && <div className="gpr-dds-editor-modal" role="dialog" aria-modal="true" aria-label="Редактор ГПР"><button type="button" className="gpr-dds-editor-close" onClick={() => setFinanceEditorOpen(false)}>Закрыть</button><FinanceOperations
          finance={finance}
          preview={null}
          selectedRows={financeStructuredRows}
          setSelectedRows={setFinanceStructuredRows}
          selectedContractId={selectedFinanceContractId}
          kind={financeKind}
          title={financeTitle}
          amount={financeAmount}
          date={financeDate}
          direction={financeDirection}
          extra={financeExtra}
          objectName={financeObject}
          category={financeCategory}
          note={financeNote}
          sourceDocumentId={0}
          scheduleItemId={financeScheduleItemId}
          budgetLineId={financeBudgetLineId}
          baselineId={financeBaselineId}
          costCategories={costCategories}
          setKind={setFinanceKind}
          setDirection={setFinanceDirection}
          setTitle={setFinanceTitle}
          setAmount={setFinanceAmount}
          setDate={setFinanceDate}
          setExtra={setFinanceExtra}
          setObjectName={setFinanceObject}
          setCategory={setFinanceCategory}
          setNote={setFinanceNote}
          setScheduleItemId={setFinanceScheduleItemId}
          setBudgetLineId={setFinanceBudgetLineId}
          setBaselineId={setFinanceBaselineId}
          onClosePreview={() => undefined}
          onImport={() => undefined}
          onAdd={() => void addFinanceItem()}
          onConfirm={(kind, id, status) => void confirmFinance(kind, id, status)}
          onRefreshVat={(kind, id) => void refreshFinanceVat(kind, id)}
          onConfirmPayment={(id, amount) => void confirmCashPayment(id, amount)}
          includeEditor={active === "График работ" || (active === "ГПР и ДДС" && gprDdsTab === "gpr")}
          includeRegisters={false}
          editorScope="gpr"
        /></div>}
        </>}
        dds={<>
        <FinanceModule
          finance={finance}
          candidates={financeCandidates}
          contracts={contracts}
          selectedContractId={selectedFinanceContractId}
          onSelectContract={setSelectedFinanceContractId}
          onPrepare={prepareFinanceItem}
          onUseCandidate={(candidate) => void useFinanceCandidate(candidate)}
          onUpload={onUploadFinanceDocument}
          onUploadFinance={(files, contractId, kind) => void uploadContractFinance(files, contractId, kind)}
          onOpenSchedule={() => setGprDdsTab("gpr")}
          onReload={() => void loadFinance()}
          embedded
        />
        <DdsWorkspace
          key={projectId}
          finance={finance}
          selectedContractId={selectedFinanceContractId}
          onRefreshVat={(kind, id) => void refreshFinanceVat(kind, id)}
          onPrepare={(kind) => { prepareFinanceItem(kind); setFinanceEditorOpen(true); }}
          onConfirm={confirmFinance}
          onConfirmMany={confirmFinanceMany}
          onConfirmPayment={(id, amount) => void confirmCashPayment(id, amount)}
          onReversePayment={(id) => void reverseCashPayment(id)}
          onLinkControls={(id, contractId, scheduleItemId, budgetLineId) =>
            void linkCashFlowControls(id, contractId, scheduleItemId, budgetLineId)
          }
          onLinkApprovedBudgetLine={(id, budgetLineId) => void linkApprovedCashFlowBudgetLine(id, budgetLineId)}
          onMutatePlan={mutateCashFlowPlan}
          onUndoPlanMutation={undoCashFlowPlanMutation}
          focusScheduleItemId={focusedScheduleItemId}
          onOpenSchedule={(scheduleItemId) => { setFocusedScheduleItemId(scheduleItemId); setGprDdsTab("gpr"); }}
          onDropInvoices={uploadDdsInvoices}
          onReviewInvoice={(documentId) => reviewUploadedFinanceDocuments([documentId])}
          onImportCashFlow={(files) => void uploadContractFinance(files, selectedFinanceContractId, "cash-flow")}
          onPrepareAdditionalExpense={() => { prepareFinanceItem("cash-out"); setFinanceCategory("Дополнительные расходы"); setFinanceEditorOpen(true); }}
        />
        {financeEditorOpen && <div className="gpr-dds-editor-modal" role="dialog" aria-modal="true" aria-label="Проверка финансовых данных">
          <fieldset className="finance-editor-fields" disabled={invoiceConfirming}>
          {invoiceConfirming && <p className="form-operation-pending" role="status">Подтверждаю счёт…</p>}
          {invoiceConfirmationError && <div className="form-operation-error" role="alert">{invoiceConfirmationError}</div>}
          {!financeStructuredPreview && !invoiceExtractionProposal && <button type="button" className="gpr-dds-editor-close" onClick={() => setFinanceEditorOpen(false)}>Закрыть</button>}<FinanceOperations
          projectId={projectId}
          onArticleBudgetApplied={() => void loadFinance()}
          finance={finance}
          preview={financeStructuredPreview}
          selectedRows={financeStructuredRows}
          setSelectedRows={setFinanceStructuredRows}
          selectedContractId={selectedFinanceContractId}
          kind={financeKind}
          title={financeTitle}
          amount={financeAmount}
          date={financeDate}
          direction={financeDirection}
          extra={financeExtra}
          objectName={financeObject}
          category={financeCategory}
          note={financeNote}
          sourceDocumentId={financeSourceDocumentId}
          scheduleItemId={financeScheduleItemId}
          budgetLineId={financeBudgetLineId}
          baselineId={financeBaselineId}
          costCategories={costCategories}
          invoiceProposal={invoiceExtractionProposal}
          setKind={setFinanceKind}
          setDirection={setFinanceDirection}
          setTitle={setFinanceTitle}
          setAmount={setFinanceAmount}
          setDate={setFinanceDate}
          setExtra={setFinanceExtra}
          setObjectName={setFinanceObject}
          setCategory={setFinanceCategory}
          setNote={setFinanceNote}
          setScheduleItemId={setFinanceScheduleItemId}
          setBudgetLineId={setFinanceBudgetLineId}
          setBaselineId={setFinanceBaselineId}
          onEditInvoice={editInvoiceExtraction}
          onConfirmInvoice={() => void confirmInvoiceExtraction().then((confirmed) => { if (confirmed) setFinanceEditorOpen(false); })}
          onRejectInvoice={() => void rejectInvoiceExtraction()}
          onRetryInvoiceAi={() => void retryInvoiceAiAnalysis()}
          invoiceAiRetrying={invoiceAiRetrying}
          onCloseInvoice={() => { setInvoiceExtractionProposal(null); setFinanceEditorOpen(false); }}
          onAddCostCategory={(name) => void addCostCategory(name)}
          onClosePreview={() => {
            setFinanceStructuredPreview(null);
            setFinanceStructuredRows([]);
            setFinanceEditorOpen(false);
          }}
          onImport={() => void importStructuredFinance()}
          onEditPreviewRow={editStructuredFinanceRow}
          onAdd={() => void addFinanceItem().then(() => {
            if (financeKind === "cash-in" || financeKind === "cash-out") {
              setFinanceEditorOpen(false);
            }
          })}
          onRefreshVat={(kind, id) => void refreshFinanceVat(kind, id)}
          onConfirm={(kind, id, status) =>
            void confirmFinance(kind, id, status)
          }
          onConfirmPayment={(id, amount) =>
            void confirmCashPayment(id, amount)
          }
          includeEditor={active === "Исполнение и финансы" || (active === "ГПР и ДДС" && gprDdsTab === "dds")}
          includeScheduleRegister={false}
          includeCashFlowRegister={false}
          editorScope="finance"
        /></fieldset></div>}
        </>}
      />
      </div>
    </section>
  );
}
