import { useEffect, useMemo, useState } from "react";
import { ProviderActionCenter } from "../provider-actions";
import { AttentionPanel } from "./AttentionPanel";
import { DeadlineDigestPanel } from "./DeadlineDigestPanel";
import { ObligationDetailPanel } from "./ObligationDetailPanel";
import { RiskDecisionPanel } from "./RiskDecisionPanel";
import type { AttentionItem } from "./managementReadModel";
import { useManagementCenter } from "./useManagementCenter";

type Props = {
  projectId: number | null;
  enabled?: boolean;
  canManage?: boolean;
};

export function ManagementCenter({ projectId, enabled = true, canManage = true }: Props) {
  const controller = useManagementCenter(projectId, enabled);
  const [selected, setSelected] = useState<AttentionItem | null>(null);
  const [filter, setFilter] = useState<"all" | AttentionItem["entityType"]>("all");

  useEffect(() => { setSelected(null); setFilter("all"); }, [projectId]);

  const attention = useMemo(
    () => filter === "all" ? controller.state.attention : controller.state.attention.filter((item) => item.entityType === filter),
    [controller.state.attention, filter],
  );

  const obligation = useMemo(
    () => selected?.entityType === "obligation"
      ? controller.state.obligations.find((item) => item.id === selected.entityId) || null
      : null,
    [controller.state.obligations, selected],
  );

  function select(item: AttentionItem) {
    setSelected(item);
    if (item.entityType === "obligation" || item.entityType === "risk" || item.entityType === "decision") {
      void controller.loadHistory(item.entityType, item.entityId);
    }
  }

  return <section className="management-center" aria-label="Центр управления проектом">
    <AttentionPanel
      state={controller.state.loadState}
      items={attention}
      total={filter === "all" ? controller.state.attentionTotal : attention.length}
      error={controller.state.error}
      onRetry={() => void controller.reload()}
      onSelect={select}
      filter={filter}
      onFilterChange={(value) => { setFilter(value); setSelected(null); }}
    />
    {selected?.entityType === "obligation" && <ObligationDetailPanel
      obligation={obligation}
      history={controller.state.history}
      historyState={controller.state.historyState}
      historyError={controller.state.historyError}
      mutationState={controller.state.mutationState}
      mutationMessage={controller.state.mutationMessage}
      onLoadHistory={(id) => void controller.loadHistory("obligation", id)}
      onTransition={(item, status) => void controller.transitionObligation(item, status)}
      canManage={canManage}
    />}
    {(selected?.entityType === "risk" || selected?.entityType === "decision") && <RiskDecisionPanel
      item={selected}
      history={controller.state.history}
      historyState={controller.state.historyState}
      mutationState={controller.state.mutationState}
      mutationMessage={controller.state.mutationMessage}
      onLoadHistory={(item) => void controller.loadHistory(item.entityType as "risk" | "decision", item.entityId)}
      onTransition={(item, status) => void controller.transitionGovernance(item, status)}
      canManage={canManage}
    />}
    {selected?.entityType === "task" && <section className="management-card management-empty">
      <h2>{selected.title}</h2>
      <p>Откройте раздел «Задачи», чтобы изменить исполнителя или статус. Источник и версия результата сохраняются в истории задачи.</p>
    </section>}
    <DeadlineDigestPanel
      deadlinePolicy={obligation?.deadlinePolicy || null}
      digestState={null}
      digestJob={controller.state.digestJob}
      notifications={controller.state.notifications}
      configurationAvailable={controller.state.digestPreference !== null}
      preference={controller.state.digestPreference}
      mutationState={controller.state.mutationState}
      mutationMessage={controller.state.mutationMessage}
      onSave={(preference) => void controller.saveDigestPreference(preference)}
      onEnqueue={(preference) => void controller.enqueueDigest(preference)}
    />
    <ProviderActionCenter projectId={projectId} enabled={enabled} />
  </section>;
}
