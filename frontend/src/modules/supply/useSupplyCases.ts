import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import {
  parseProjectOrganization,
  parseSupplyEvidenceOptions,
  parseSupplyList,
  type SupplyAction,
  type SupplyCaseView,
  type SupplyEvidenceOption,
} from "./supplyReadModel";
import type { SupplyActionFields } from "./SupplyActionForm";

export type SupplyLoadState = "idle" | "loading" | "ready" | "empty" | "error";
export type SupplyMutationState = "idle" | "saving" | "saved" | "blocked" | "conflict" | "error";

const managerActions = new Set<SupplyAction>(["approve_request", "approve_order", "approve_act", "review", "resolve_discrepancy"]);
const actionPaths: Record<SupplyAction, string> = {
  review: "review",
  approve_request: "approve-request",
  prepare_order: "order",
  approve_order: "approve-order",
  record_order: "record-order",
  record_delivery: "deliveries",
  resolve_discrepancy: "resolve-discrepancy",
  propose_act: "acceptance-acts",
  approve_act: "approve-acceptance-act",
};

function safeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Сервер вернул данные в неожиданном формате. Обновите цепочку снабжения.";
}

function commandKey(projectId: number, item: SupplyCaseView, action: SupplyAction): string {
  const nonce = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `supply:${projectId}:${item.id}:${action}:${item.recordVersion}:${nonce}`;
}

type MutationResult = {
  supply_case_id: number;
  record_version: number;
  external_action_created: false;
};

function parseMutationResult(value: unknown, item: SupplyCaseView): MutationResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid mutation result");
  const result = value as Record<string, unknown>;
  if (result.supply_case_id !== item.id || !Number.isInteger(result.record_version)
    || Number(result.record_version) < item.recordVersion || result.external_action_created !== false) {
    throw new Error("unsafe mutation result");
  }
  return result as MutationResult;
}

export function useSupplyCases(
  projectId: number | null,
  enabled: boolean,
  canManage: boolean,
) {
  const sequence = useRef(0);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  const [loadState, setLoadState] = useState<SupplyLoadState>("idle");
  const [items, setItems] = useState<SupplyCaseView[]>([]);
  const [organizationId, setOrganizationId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mutationState, setMutationState] = useState<SupplyMutationState>("idle");
  const [mutationMessage, setMutationMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [evidence, setEvidence] = useState<SupplyEvidenceOption[]>([]);
  const [evidenceLoading, setEvidenceLoading] = useState(false);

  useEffect(() => {
    setBusyId(null);
    setMutationState("idle");
    setMutationMessage(null);
    setEvidence([]);
    setEvidenceLoading(false);
  }, [enabled, projectId]);

  const reload = useCallback(async () => {
    const requestSequence = ++sequence.current;
    if (!enabled || !projectId) {
      setLoadState("idle");
      setItems([]);
      setOrganizationId(null);
      setError(null);
      return;
    }
    setLoadState("loading");
    setError(null);
    try {
      const [projectRaw, supplyRaw] = await Promise.all([
        api<unknown>(`/projects/${projectId}`),
        api<unknown>(`/api/mvp4/supply?project_id=${projectId}`),
      ]);
      const nextOrganizationId = parseProjectOrganization(projectRaw, projectId);
      const nextItems = parseSupplyList(supplyRaw, projectId);
      if (requestSequence !== sequence.current || activeProject.current !== projectId) return;
      setOrganizationId(nextOrganizationId);
      setItems(nextItems);
      setLoadState(nextItems.length ? "ready" : "empty");
    } catch (caught) {
      if (requestSequence !== sequence.current || activeProject.current !== projectId) return;
      setItems([]);
      setOrganizationId(null);
      setLoadState("error");
      setError(safeError(caught));
    }
  }, [enabled, projectId]);

  useEffect(() => { void reload(); }, [reload]);

  const loadEvidence = useCallback(async () => {
    if (!projectId || !organizationId) return [];
    const selectedProject = projectId;
    setEvidenceLoading(true);
    try {
      const raw = await api<unknown>(`/api/v54/evidence?project_id=${selectedProject}`, { cache: "no-store" });
      const options = parseSupplyEvidenceOptions(raw, selectedProject);
      if (activeProject.current !== selectedProject) return [];
      setEvidence(options);
      return options;
    } catch (caught) {
      if (activeProject.current === selectedProject) {
        setEvidence([]);
        setMutationState("error");
        setMutationMessage(safeError(caught));
      }
      return [];
    } finally {
      if (activeProject.current === selectedProject) setEvidenceLoading(false);
    }
  }, [organizationId, projectId]);

  const runAction = useCallback(async (action: SupplyAction, item: SupplyCaseView, fields: SupplyActionFields = {}) => {
    if (!projectId || !organizationId || item.projectId !== projectId) {
      setMutationState("blocked");
      setMutationMessage("Контекст проекта изменился. Обновите цепочку снабжения.");
      return;
    }
    if (managerActions.has(action) && !canManage) {
      setMutationState("blocked");
      setMutationMessage("Для согласования требуется роль менеджера проекта.");
      return;
    }
    const key = commandKey(projectId, item, action);
    setBusyId(item.id);
    setMutationState("saving");
    setMutationMessage(null);
    try {
      const query = new URLSearchParams({
        organization_id: String(organizationId),
        project_id: String(projectId),
      });
      const raw = await api<unknown>(`/api/mvp4/supply/${item.id}/${actionPaths[action]}?${query}`, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({ command_key: key, expected_version: item.recordVersion, ...fields }),
      });
      parseMutationResult(raw, item);
      if (activeProject.current !== projectId) return;
      setMutationState("saved");
      setMutationMessage("Решение сохранено во внутренней истории. Внешнее действие не выполнялось.");
      await reload();
    } catch (caught) {
      if (activeProject.current !== projectId) return;
      const conflict = caught instanceof ApiError && caught.status === 409;
      setMutationState(conflict ? "conflict" : "error");
      setMutationMessage(conflict
        ? "Запись уже изменилась. Данные обновлены; проверьте новую версию перед повторным решением."
        : safeError(caught));
      if (conflict) await reload();
    } finally {
      if (activeProject.current === projectId) setBusyId(null);
    }
  }, [canManage, organizationId, projectId, reload]);

  return {
    loadState, items, error, mutationState, mutationMessage, busyId, reload, runAction,
    evidence, evidenceLoading, loadEvidence,
  };
}
