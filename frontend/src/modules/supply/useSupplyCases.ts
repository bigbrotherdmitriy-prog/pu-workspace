import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import {
  parseProjectOrganization,
  parseSupplyList,
  type SupplyAction,
  type SupplyCaseView,
} from "./supplyReadModel";

export type SupplyLoadState = "idle" | "loading" | "ready" | "empty" | "error";
export type SupplyMutationState = "idle" | "saving" | "saved" | "blocked" | "conflict" | "error";

const executableActions = new Set<SupplyAction>(["approve_request", "approve_order", "approve_act"]);
const actionPaths: Record<"approve_request" | "approve_order" | "approve_act", string> = {
  approve_request: "approve-request",
  approve_order: "approve-order",
  approve_act: "approve-acceptance-act",
};

const blockedReasons: Record<Exclude<SupplyAction, "approve_request" | "approve_order" | "approve_act">, string> = {
  review: "Откройте связанное доказательство и укажите решение проверки. Без явного решения заявка не изменяется.",
  prepare_order: "Для подготовки заказа нужны количество и номер заказа. Введите их в специализированной форме — данные не подставляются автоматически.",
  record_order: "Нужно новое точное доказательство размещения заказа. Сначала привяжите подтверждающий документ.",
  record_delivery: "Нужны количество поставки и новое точное доказательство. Без них поставка не фиксируется.",
  resolve_discrepancy: "Выберите решение по расхождению в специализированной форме. Система не принимает его автоматически.",
  propose_act: "Нужны номер акта, принятое количество и точное доказательство. Без этих данных акт не создаётся.",
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

  useEffect(() => {
    setBusyId(null);
    setMutationState("idle");
    setMutationMessage(null);
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

  const runAction = useCallback(async (action: SupplyAction, item: SupplyCaseView) => {
    if (!projectId || !organizationId || item.projectId !== projectId) {
      setMutationState("blocked");
      setMutationMessage("Контекст проекта изменился. Обновите цепочку снабжения.");
      return;
    }
    if (!executableActions.has(action)) {
      setMutationState("blocked");
      setMutationMessage(blockedReasons[action as keyof typeof blockedReasons]);
      return;
    }
    if (!canManage) {
      setMutationState("blocked");
      setMutationMessage("Для согласования требуется роль менеджера проекта.");
      return;
    }
    const executable = action as keyof typeof actionPaths;
    const key = commandKey(projectId, item, action);
    setBusyId(item.id);
    setMutationState("saving");
    setMutationMessage(null);
    try {
      const query = new URLSearchParams({
        organization_id: String(organizationId),
        project_id: String(projectId),
      });
      const raw = await api<unknown>(`/api/mvp4/supply/${item.id}/${actionPaths[executable]}?${query}`, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({ command_key: key, expected_version: item.recordVersion }),
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
  };
}
