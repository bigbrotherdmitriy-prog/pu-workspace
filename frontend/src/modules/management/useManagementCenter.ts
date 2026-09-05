import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../../api/client";
import {
  parseAttentionResponse,
  parseHistoryResponse,
  parseDigestEnqueueResult,
  parseMeetingProposalConfirmation,
  parseMeetingProposalEnvelope,
  parseNotificationsResponse,
  parseObligationsResponse,
  type AttentionItem,
  type DigestNotification,
  type DigestEnqueueResult,
  type HistoryEvent,
  type MeetingActionCandidate,
  type MeetingProposal,
  type Obligation,
} from "./managementReadModel";

export type LoadState = "idle" | "loading" | "ready" | "empty" | "error";
export type MutationState = "idle" | "saving" | "saved" | "conflict" | "error";

export type ManagementCenterState = {
  loadState: LoadState;
  attention: AttentionItem[];
  attentionTotal: number;
  generatedAt: string | null;
  obligations: Obligation[];
  notifications: DigestNotification[];
  error: string | null;
  historyState: LoadState;
  history: HistoryEvent[];
  historyError: string | null;
  mutationState: MutationState;
  mutationMessage: string | null;
  proposalState: LoadState;
  proposals: MeetingProposal[];
  digestJob: DigestEnqueueResult | null;
};

const initialState: ManagementCenterState = {
  loadState: "idle",
  attention: [],
  attentionTotal: 0,
  generatedAt: null,
  obligations: [],
  notifications: [],
  error: null,
  historyState: "idle",
  history: [],
  historyError: null,
  mutationState: "idle",
  mutationMessage: null,
  proposalState: "idle",
  proposals: [],
  digestJob: null,
};

function safeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Сервер вернул данные в неожиданном формате. Обновите страницу или обратитесь к администратору.";
}

export function useManagementCenter(projectId: number | null, enabled = true) {
  const [state, setState] = useState<ManagementCenterState>(initialState);

  const reload = useCallback(async () => {
    if (!enabled || !projectId) {
      setState(initialState);
      return;
    }
    setState((current) => ({ ...current, loadState: "loading", error: null }));
    try {
      const [attentionRaw, obligationsRaw, notificationsRaw] = await Promise.all([
        api<unknown>(`/management/v2/attention?project_id=${projectId}`),
        api<unknown>(`/management/obligations?project_id=${projectId}`),
        api<unknown>(`/management/notifications?project_id=${projectId}`),
      ]);
      const attention = parseAttentionResponse(attentionRaw);
      const obligations = parseObligationsResponse(obligationsRaw);
      const notifications = parseNotificationsResponse(notificationsRaw);
      setState((current) => ({
        ...current,
        loadState: attention.items.length || obligations.length || notifications.length ? "ready" : "empty",
        attention: attention.items,
        attentionTotal: attention.total,
        generatedAt: attention.generatedAt,
        obligations,
        notifications,
        error: null,
      }));
    } catch (error) {
      setState((current) => ({ ...current, loadState: "error", error: safeError(error) }));
    }
  }, [enabled, projectId]);

  useEffect(() => { void reload(); }, [reload]);

  const loadHistory = useCallback(async (entityType: "obligation" | "risk" | "decision", entityId: number) => {
    if (!projectId) return;
    setState((current) => ({ ...current, historyState: "loading", history: [], historyError: null }));
    const path = entityType === "obligation"
      ? `/management/v2/obligations/${entityId}/history`
      : `/management/v2/${entityType}s/${entityId}/history?project_id=${projectId}`;
    try {
      const history = parseHistoryResponse(await api<unknown>(path));
      setState((current) => ({ ...current, history, historyState: history.length ? "ready" : "empty" }));
    } catch (error) {
      setState((current) => ({ ...current, historyState: "error", historyError: safeError(error) }));
    }
  }, [projectId]);

  const transitionObligation = useCallback(async (
    obligation: Obligation,
    status: string,
    options: { reason?: string; resultNote?: string } = {},
  ) => {
    setState((current) => ({ ...current, mutationState: "saving", mutationMessage: null }));
    try {
      await api<unknown>(`/management/v2/obligations/${obligation.id}`, {
        method: "PATCH",
        body: JSON.stringify({ expected_version: obligation.recordVersion, status,
          reason: options.reason || null, result_note: options.resultNote || null }),
      });
      setState((current) => ({ ...current, mutationState: "saved", mutationMessage: "Изменение сохранено." }));
      await reload();
    } catch (error) {
      const conflict = error instanceof ApiError && error.status === 409;
      setState((current) => ({ ...current, mutationState: conflict ? "conflict" : "error",
        mutationMessage: conflict
          ? "Запись уже изменена другим пользователем. Обновите данные и повторите действие."
          : safeError(error) }));
    }
  }, [reload]);

  const transitionGovernance = useCallback(async (
    item: AttentionItem,
    status: string,
    options: { reason?: string; actionNote?: string; decisionText?: string } = {},
  ) => {
    if (!projectId || (item.entityType !== "risk" && item.entityType !== "decision")) return;
    setState((current) => ({ ...current, mutationState: "saving", mutationMessage: null }));
    try {
      await api<unknown>(`/management/v2/${item.entityType}s/${item.entityId}`, {
        method: "PATCH",
        body: JSON.stringify({ project_id: projectId, expected_version: item.recordVersion, status,
          reason: options.reason || null, action_note: options.actionNote || null,
          decision_text: options.decisionText || null }),
      });
      setState((current) => ({ ...current, mutationState: "saved", mutationMessage: "Изменение сохранено." }));
      await reload();
    } catch (error) {
      const conflict = error instanceof ApiError && error.status === 409;
      setState((current) => ({ ...current, mutationState: conflict ? "conflict" : "error",
        mutationMessage: conflict
          ? "Запись уже изменена другим пользователем. Обновите данные и повторите действие."
          : safeError(error) }));
    }
  }, [projectId, reload]);

  const proposeMeetingActions = useCallback(async (meetingId: number, candidates: MeetingActionCandidate[]) => {
    if (!projectId) return;
    setState((current) => ({ ...current, proposalState: "loading", mutationMessage: null }));
    try {
      const proposals = parseMeetingProposalEnvelope(await api<unknown>(`/management/v2/meetings/${meetingId}/proposals`, {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, candidates }),
      }));
      setState((current) => ({ ...current, proposals, proposalState: proposals.length ? "ready" : "empty" }));
    } catch (error) {
      setState((current) => ({ ...current, proposalState: "error", mutationMessage: safeError(error) }));
    }
  }, [projectId]);

  const confirmMeetingProposal = useCallback(async (proposal: MeetingProposal, createInternalTask: boolean) => {
    if (!projectId) return;
    setState((current) => ({ ...current, mutationState: "saving", mutationMessage: null }));
    try {
      const confirmed = parseMeetingProposalConfirmation(await api<unknown>(
        `/management/v2/proposals/${proposal.entityType}/${proposal.entityId}/confirm`, {
          method: "POST",
          body: JSON.stringify({ project_id: projectId, expected_version: proposal.recordVersion,
            create_internal_task: createInternalTask }),
        },
      ));
      setState((current) => ({ ...current, mutationState: "saved", mutationMessage: "Предложение подтверждено.",
        proposals: current.proposals.map((item) => item.entityType === confirmed.entityType && item.entityId === confirmed.entityId
          ? confirmed : item) }));
    } catch (error) {
      const conflict = error instanceof ApiError && error.status === 409;
      setState((current) => ({ ...current, mutationState: conflict ? "conflict" : "error",
        mutationMessage: conflict ? "Предложение уже изменено. Получите новую версию перед подтверждением." : safeError(error) }));
    }
  }, [projectId]);

  const enqueueDigest = useCallback(async (preference: {
    timezone: string;
    quietStart: string;
    quietEnd: string;
    channel: "in_app" | "disabled";
    localDate: string;
  }) => {
    if (!projectId) return;
    setState((current) => ({ ...current, mutationState: "saving", mutationMessage: null }));
    try {
      const digestJob = parseDigestEnqueueResult(await api<unknown>("/management/v2/digests", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, timezone: preference.timezone,
          quiet_start: preference.quietStart, quiet_end: preference.quietEnd,
          channel: preference.channel, local_date: preference.localDate }),
      }));
      setState((current) => ({ ...current, digestJob, mutationState: "saved",
        mutationMessage: "Сводка поставлена в надёжную очередь." }));
    } catch (error) {
      setState((current) => ({ ...current, mutationState: "error", mutationMessage: safeError(error) }));
    }
  }, [projectId]);

  return { state, reload, loadHistory, transitionObligation, transitionGovernance,
    proposeMeetingActions, confirmMeetingProposal, enqueueDigest };
}
