import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import { MeetingProposalPanel } from "../management/MeetingProposalPanel";
import { meetingProposalBlockReason, parseMeetingProposalConfirmation, parseMeetingProposalEnvelope,
  type MeetingActionCandidate, type MeetingProposal } from "../management/managementReadModel";
import { parseEligibleSources, parseMeetingBinding, sameBinding, type MeetingBinding, type MeetingSource } from "./meetingSourceModel";

type Props = { projectId: number; meetingId: number; recordVersion: number;
  members: { user_id: number; name: string }[]; onVersionChange?: (version: number) => void;
  client?: typeof api };

/** Scoped by project/meeting/version in the parent key; no automatic mutations. */
export function MeetingSourcePanel({ projectId, meetingId, recordVersion, members, onVersionChange, client = api }: Props) {
  const alive = useRef(true), sequence = useRef(0), inFlight = useRef(false);
  const context = `${projectId}:${meetingId}:${recordVersion}`, activeContext = useRef(context);
  activeContext.current = context;
  const isCurrent = () => alive.current && activeContext.current === context;
  const [sources, setSources] = useState<MeetingSource[] | null>(null);
  const [selected, setSelected] = useState("");
  const [selectedPins, setSelectedPins] = useState<number[]>([]);
  const [binding, setBinding] = useState<MeetingBinding | null>(null);
  const [proposals, setProposals] = useState<MeetingProposal[]>([]);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [title, setTitle] = useState(""), [owner, setOwner] = useState("");
  const [kind, setKind] = useState<MeetingActionCandidate["kind"]>("task");
  const identity = `${projectId}:${meetingId}`, previousIdentity = useRef(identity);
  useEffect(() => { alive.current = true; return () => { alive.current = false; sequence.current++; }; }, []);
  useEffect(() => {
    const changedIdentity = previousIdentity.current !== identity;
    previousIdentity.current = identity;
    if (changedIdentity) { setTitle(""); setOwner(""); }
    if (!changedIdentity && binding?.meetingRecordVersion === recordVersion && binding.meetingId === meetingId) return;
    sequence.current++; setSources(null); setSelected(""); setSelectedPins([]); setBinding(null); setProposals([]); setNotice("");
  }, [recordVersion, meetingId, projectId]);

  async function action(work: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setNotice("");
    try { await work(); }
    catch (error) {
      if (isCurrent()) {
        setBinding(null); setProposals([]); setSources(null); setSelected(""); setSelectedPins([]);
        setNotice(error instanceof ApiError && error.status === 409
          ? "Протокол или источник изменился. Обновите совещания перед повторным выбором."
          : "Источник или действие недоступны. Проверьте права, версию и срок хранения; обновите данные.");
      }
    } finally { inFlight.current = false; if (alive.current) setBusy(false); }
  }

  async function loadSources() {
    const request = ++sequence.current;
    await action(async () => {
      setBinding(null); setProposals([]); setSelected(""); setSelectedPins([]);
      const [raw, history] = await Promise.all([
        client<unknown>(`/management/v2/meetings/${meetingId}/eligible-sources?project_id=${projectId}`),
        client<unknown>(`/management/v2/meetings/${meetingId}/proposals?project_id=${projectId}`),
      ]);
      const found = parseEligibleSources(raw, meetingId, recordVersion);
      const rows = parseMeetingProposalEnvelope(history);
      if (!isCurrent() || request !== sequence.current) return;
      setSources(found);
      const envelope = history as Record<string, unknown>;
      if (envelope.meeting_id !== meetingId || envelope.meeting_record_version !== recordVersion) throw new Error("history_scope_mismatch");
      if (envelope.origin_status === "bound") {
        const existing = parseMeetingBinding(envelope);
        if (existing.meetingId !== meetingId || existing.meetingRecordVersion !== recordVersion
          || rows.some(row => !meetingProposalBlockReason(row) && !sameBinding(existing, row.binding))
          || !found.some(s => s.sourceId === existing.sourceId && s.sourceVersionId === existing.sourceVersionId)) {
          throw new Error("existing_binding_mismatch");
        }
        setBinding(existing); setSelected(`${existing.sourceId}:${existing.sourceVersionId}`); setProposals(rows);
        setNotice("Существующая привязка и предложения загружены. Повторная привязка не выполнялась.");
      }
      if (!found.length) setNotice("Нет доступных источников с подтверждённой версией и правами. Загрузите документ протокола; если он уже загружен, проверьте доступ и обработку.");
    });
  }

  const chosen = sources?.find(s => `${s.sourceId}:${s.sourceVersionId}` === selected);
  async function bindSource() {
    if (!chosen || !chosen.evidencePins.length) return;
    await action(async () => {
      const raw = await client<unknown>(`/management/v2/meetings/${meetingId}/source-binding`, {
        method: "POST", body: JSON.stringify({ project_id: projectId, expected_version: recordVersion,
          command_id: crypto.randomUUID(), source_id: chosen.sourceId, source_version_id: chosen.sourceVersionId }),
      });
      const result = parseMeetingBinding(raw);
      if ((raw as Record<string, unknown>).external_actions_created !== false || result.meetingId !== meetingId
        || result.meetingRecordVersion !== recordVersion + 1 || result.sourceId !== chosen.sourceId
        || result.sourceVersionId !== chosen.sourceVersionId) throw new Error("binding_mismatch");
      if (!isCurrent()) return;
      setBinding(result); setNotice("Источник привязан к этой версии протокола. Новые действия требуют отдельного подтверждения.");
      onVersionChange?.(result.meetingRecordVersion);
    });
  }

  async function propose() {
    if (!binding || !chosen || !title.trim() || !selectedPins.length || selectedPins.length > 20
      || !members.some(m => String(m.user_id) === owner)) return;
    await action(async () => {
      const raw = await client<unknown>(`/management/v2/meetings/${meetingId}/proposals`, {
        method: "POST", body: JSON.stringify({ project_id: projectId, meeting_source_binding_id: binding.bindingId,
          candidates: [{ kind, title: title.trim(), owner_user_id: Number(owner), evidence_pins: selectedPins.map(i => chosen.evidencePins[i]) }] }),
      });
      const rows = parseMeetingProposalEnvelope(raw);
      if (rows.some(p => !sameBinding(p.binding, binding) || meetingProposalBlockReason(p))) throw new Error("proposal_mismatch");
      if (!isCurrent()) return;
      setProposals(rows); setTitle(""); setNotice("Предложение подготовлено. Задача или решение ещё не подтверждены.");
    });
  }

  async function confirm(proposal: MeetingProposal, createInternalTask: boolean) {
    if (!binding || meetingProposalBlockReason(proposal) || !sameBinding(proposal.binding, binding)) return;
    await action(async () => {
      const result = parseMeetingProposalConfirmation(await client<unknown>(
        `/management/v2/proposals/${proposal.entityType}/${proposal.entityId}/confirm`, {
          method: "POST", body: JSON.stringify({ project_id: projectId, expected_version: proposal.recordVersion,
            create_internal_task: createInternalTask }),
        }));
      if (!sameBinding(result.binding, binding) || result.entityId !== proposal.entityId
        || result.entityType !== proposal.entityType || result.recordVersion <= proposal.recordVersion
        || (createInternalTask && result.taskId === null)) throw new Error("confirmation_mismatch");
      if (!isCurrent()) return;
      setProposals(rows => rows.map(p => p.entityId === result.entityId && p.entityType === result.entityType ? result : p));
      setNotice("Предложение подтверждено. Внешние письма и финансовые операции не выполнялись.");
    });
  }

  return <section aria-label={`Источник протокола ${meetingId}`}>
    <h3>Источник и действия по протоколу</h3>
    <p>Привязка относится к версии {recordVersion}. Изменение протокола потребует новой привязки.</p>
    <p>Доступность доказательства проверяет сервер. Сохранённый фрагмент может быть доступен после удаления оригинала; это не означает доступ к исходному файлу.</p>
    <button type="button" disabled={busy} onClick={() => void loadSources()}>Выбрать источник протокола</button>
    {!!sources?.length && <>
      <label>Подтверждённый источник<select aria-label="Подтверждённый источник" disabled={busy || !!binding}
        value={selected} onChange={e => { setSelected(e.target.value); setSelectedPins([]); }}>
        <option value="">Выберите источник и версию</option>
        {sources.map(s => <option disabled={!s.evidencePins.length} key={`${s.sourceId}:${s.sourceVersionId}`} value={`${s.sourceId}:${s.sourceVersionId}`}>
          Источник {s.sourceId} · версия {s.sourceVersionId}{!s.evidencePins.length ? " — нет доступных доказательств" : ""}</option>)}
      </select></label>
      <button type="button" disabled={busy || !chosen?.evidencePins.length || !!binding} onClick={() => void bindSource()}>Привязать выбранный источник</button>
    </>}
    {binding && <fieldset disabled={busy}>
      <legend>Предложить действие — без автоматического исполнения</legend>
      <label>Тип действия<select aria-label="Тип действия" value={kind} onChange={e => setKind(e.target.value as MeetingActionCandidate["kind"])}>
        <option value="task">Внутренняя задача</option><option value="obligation">Обязательство</option><option value="decision">Решение</option>
      </select></label>
      <label>Название действия<input value={title} maxLength={300} onChange={e => setTitle(e.target.value)} /></label>
      <label>Ответственный<select aria-label="Ответственный" value={owner} onChange={e => setOwner(e.target.value)}>
        <option value="">Выберите участника</option>{members.map(m => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
      </select></label>
      <p>Выберите доказательства для действия (не более 20). Формулы и выводы требуют проверки человеком.</p>
      {chosen?.evidencePins.map((pin, i) => <label key={i}>
        <input type="checkbox" checked={selectedPins.includes(i)} aria-label={`Доказательство ${i + 1}`}
          disabled={!selectedPins.includes(i) && selectedPins.length >= 20}
          onChange={e => setSelectedPins(current => e.target.checked ? [...current, i] : current.filter(n => n !== i))} />
        Доказательство {i + 1}: {String(((pin.ref as Record<string, unknown>).id as Record<string, unknown>).value)}
      </label>)}
      <button type="button" disabled={!title.trim() || !owner || !selectedPins.length} onClick={() => void propose()}>Подготовить предложение</button>
    </fieldset>}
    {notice && <p role="status">{notice}</p>}
    {!!proposals.length && <fieldset disabled={busy}><MeetingProposalPanel state="ready" proposals={proposals}
      busyId={busy ? proposals[0].entityId : null} onConfirm={(p, task) => void confirm(p, task)} /></fieldset>}
  </section>;
}
