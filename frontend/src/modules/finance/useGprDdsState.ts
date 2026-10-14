import { useEffect, useState } from "react";
import { useFinanceController } from "./useFinanceController";
import { uploadInvoiceBatch } from "./invoiceUploadTransport";
import type { GprDdsTab } from "./GprDdsWorkspace";

type Options = {
  ready: boolean;
  projectId: number;
  /** Current shell section: an invoice or a structured import waiting for review opens the editor on "ГПР и ДДС". */
  active: string;
  setNotice: (message: string) => void;
  setError: (message: string) => void;
};

/** State of the ГПР и ДДС screen: the finance controller plus the tab, the editor dialog and the cross-tab focus. */
export function useGprDdsState({ ready, projectId, active, setNotice, setError }: Options) {
  const [gprDdsTab, setGprDdsTab] = useState<GprDdsTab>("gpr");
  const [financeEditorOpen, setFinanceEditorOpen] = useState(false);
  const [focusedScheduleItemId, setFocusedScheduleItemId] = useState(0);
  const controller = useFinanceController({ ready, projectId, setNotice, setError });
  const { invoiceExtractionProposal, financeStructuredPreview, loadFinance } = controller;
  useEffect(() => {
    if (active === "ГПР и ДДС" && (invoiceExtractionProposal?.requires_confirmation || financeStructuredPreview)) setFinanceEditorOpen(true);
  }, [active, invoiceExtractionProposal, financeStructuredPreview]);

  async function uploadDdsInvoices(files: File[], onProgress: (message: string) => void) {
    const result = await uploadInvoiceBatch(projectId, files, onProgress);
    await loadFinance();
    return result;
  }

  return {
    ...controller,
    gprDdsTab, setGprDdsTab,
    financeEditorOpen, setFinanceEditorOpen,
    focusedScheduleItemId, setFocusedScheduleItemId,
    uploadDdsInvoices,
  };
}

export type GprDdsState = ReturnType<typeof useGprDdsState>;
