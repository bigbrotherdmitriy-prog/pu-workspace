import { useEffect, useState } from "react";
import { api } from "../../api/client";

export type ProjectLaunchState = {
  projectName: string; sourceReady: boolean; documents: number; analyzedDocuments: number;
  contracts: number; linkedContracts: number; scheduleRows: number; budgetRows: number;
  cashFlowRows: number; contacts: number; confirmedContacts: number; inboxMessages: number;
  workspaceMode?: "managed" | "imported"; managedFolderId?: string;
};

export function useProjectLaunchReadiness(projectId: number) {
  const [state, setState] = useState<ProjectLaunchState | null>(null);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setError("");
    api(`/projects/${projectId}/launch-readiness`).then((data) => {
      if (!active) return;
      setState({
        projectName: data.project_name, sourceReady: data.source_ready,
        documents: data.documents, analyzedDocuments: data.analyzed_documents,
        contracts: data.contracts, linkedContracts: data.linked_contracts,
        scheduleRows: data.schedule_rows, budgetRows: data.budget_rows,
        cashFlowRows: data.cash_flow_rows, contacts: data.contacts,
        confirmedContacts: data.confirmed_contacts, inboxMessages: data.inbox_messages,
        workspaceMode: data.workspace_mode || undefined, managedFolderId: data.managed_folder_id || undefined,
      });
    }).catch((reason) => active && setError(reason.message));
    return () => { active = false; };
  }, [projectId, reloadKey]);
  return { state, error, reload: () => setReloadKey((value) => value + 1) };
}
