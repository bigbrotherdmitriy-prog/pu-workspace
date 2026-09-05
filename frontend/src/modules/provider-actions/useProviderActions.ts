import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import {
  canRequestReconciliation,
  parseProviderActionList,
  parseReconciliationResult,
  shouldPollProviderActions,
  type ProviderActionStatus,
  type ReconciliationResult,
} from "./providerActionReadModel";

export type ProviderActionLoadState = "idle" | "loading" | "ready" | "empty" | "error";
export type ProviderActionMutationState = "idle" | "saving" | "saved" | "conflict" | "forbidden" | "error";

function safeLoadError(): string {
  return "Не удалось получить безопасные статусы действий. Обновите данные или обратитесь к администратору.";
}

export function useProviderActions(projectId: number | null, enabled = true, pollIntervalMs = 3_000) {
  const [loadState, setLoadState] = useState<ProviderActionLoadState>("idle");
  const [items, setItems] = useState<ProviderActionStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mutationState, setMutationState] = useState<ProviderActionMutationState>("idle");
  const [mutationMessage, setMutationMessage] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const generation = useRef(0);

  const reload = useCallback(async () => {
    const requestGeneration = ++generation.current;
    if (!enabled || !projectId) {
      setLoadState("idle");
      setItems([]);
      setError(null);
      return;
    }
    setLoadState("loading");
    setError(null);
    try {
      const parsed = parseProviderActionList(
        await api<unknown>(`/provider-actions?project_id=${projectId}`),
        projectId,
      );
      if (generation.current !== requestGeneration) return;
      setItems(parsed);
      setLoadState(parsed.length ? "ready" : "empty");
    } catch {
      if (generation.current !== requestGeneration) return;
      setItems([]);
      setError(safeLoadError());
      setLoadState("error");
    }
  }, [enabled, projectId]);

  useEffect(() => {
    void reload();
    return () => { generation.current += 1; };
  }, [reload]);

  useEffect(() => {
    if (!enabled || !projectId || !shouldPollProviderActions(items)) return;
    const timer = window.setTimeout(() => { void reload(); }, Math.max(250, pollIntervalMs));
    return () => window.clearTimeout(timer);
  }, [enabled, items, pollIntervalMs, projectId, reload]);

  const reconcile = useCallback(async (action: ProviderActionStatus): Promise<ReconciliationResult | null> => {
    if (!projectId || action.projectId !== projectId || !canRequestReconciliation(action)) return null;
    const requestGeneration = generation.current;
    const key = `${action.actionId}:${action.revision}`;
    setBusyKey(key);
    setMutationState("saving");
    setMutationMessage(null);
    try {
      const result = parseReconciliationResult(await api<unknown>(
        `/provider-actions/${encodeURIComponent(action.actionId)}/revisions/${action.revision}/reconcile`,
        { method: "POST", body: "{}" },
      ), action);
      if (generation.current !== requestGeneration) return null;
      setMutationState("saved");
      setMutationMessage(result.alreadyQueued
        ? `Проверка результата уже выполняется: задание № ${result.jobId}.`
        : `Проверка результата поставлена в очередь: задание № ${result.jobId}.`);
      await reload();
      return result;
    } catch (failure) {
      if (generation.current !== requestGeneration) return null;
      if (failure instanceof ApiError && failure.status === 409) {
        setMutationState("conflict");
        setMutationMessage("Состояние действия уже изменилось. Обновите список перед повторной проверкой.");
      } else if (failure instanceof ApiError && failure.status === 403) {
        setMutationState("forbidden");
        setMutationMessage("Для проверки результата нужна роль руководителя проекта.");
      } else {
        setMutationState("error");
        setMutationMessage("Не удалось поставить проверку результата в очередь. Внешнее действие не повторялось.");
      }
      return null;
    } finally {
      if (generation.current === requestGeneration) setBusyKey(null);
    }
  }, [projectId, reload]);

  return { loadState, items, error, mutationState, mutationMessage, busyKey, reload, reconcile };
}
