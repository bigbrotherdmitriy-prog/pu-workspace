import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import "./storage-mutation.css";

type Preview = {
  project_id: number; proposal_id: number; action_id: number; record_version: number;
  kind: "rename" | "move"; before_name: string; after_name: string;
  provider: "google_drive" | "yandex_disk"; synthetic_only: true;
  execution_allowed: boolean; can_rollback: boolean;
};
type Job = { job_id: number; project_id: number; status: string; progress: number; outcome: string | null; record_version: number | null };
type Queued = { job_id: number; project_id: number; status: string; already_queued: boolean; record_version: number };

function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}

export function parseStorageMutationPreview(value: unknown, projectId: number, proposalId: number, actionId: number): Preview {
  if (!value || typeof value !== "object") throw new Error("invalid_storage_mutation_preview");
  const row = value as Record<string, unknown>;
  const keys = ["project_id", "proposal_id", "action_id", "record_version", "kind", "before_name", "after_name",
    "provider", "synthetic_only", "execution_allowed", "can_rollback"] as const;
  if (!exactKeys(row, keys) || row.project_id !== projectId || row.proposal_id !== proposalId || row.action_id !== actionId
    || !Number.isInteger(row.record_version) || Number(row.record_version) < 1
    || !["rename", "move"].includes(String(row.kind)) || !["google_drive", "yandex_disk"].includes(String(row.provider))
    || typeof row.before_name !== "string" || typeof row.after_name !== "string" || row.synthetic_only !== true
    || typeof row.execution_allowed !== "boolean" || typeof row.can_rollback !== "boolean") {
    throw new Error("invalid_storage_mutation_preview");
  }
  return row as Preview;
}

function parseJob(value: unknown, projectId: number): Job {
  if (!value || typeof value !== "object") throw new Error("invalid_storage_mutation_job");
  const row = value as Record<string, unknown>;
  if (!exactKeys(row, ["job_id", "project_id", "status", "progress", "outcome", "record_version"])
    || row.project_id !== projectId || !Number.isInteger(row.job_id) || !Number.isInteger(row.progress)
    || Number(row.progress) < 0 || Number(row.progress) > 100 || typeof row.status !== "string") {
    throw new Error("invalid_storage_mutation_job");
  }
  return row as Job;
}

export function parseQueuedMutation(value: unknown, projectId: number): Queued {
  if (!value || typeof value !== "object") throw new Error("invalid_storage_mutation_job");
  const row = value as Record<string, unknown>;
  if (!exactKeys(row, ["job_id", "project_id", "status", "already_queued", "record_version"])
    || row.project_id !== projectId || !Number.isInteger(row.job_id) || !Number.isInteger(row.record_version)
    || typeof row.status !== "string" || typeof row.already_queued !== "boolean") {
    throw new Error("invalid_storage_mutation_job");
  }
  return row as Queued;
}

export function StorageMutationPanel({ projectId, proposalId, actionId }: { projectId: number; proposalId: number; actionId: number }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [message, setMessage] = useState("");
  const generation = useRef(0);

  async function load() {
    const current = ++generation.current;
    try {
      const value = await api<unknown>(`/projects/${projectId}/storage-mutations/${proposalId}/actions/${actionId}/prepare`);
      if (current === generation.current) setPreview(parseStorageMutationPreview(value, projectId, proposalId, actionId));
    } catch { if (current === generation.current) setMessage("Предпросмотр устарел или недоступен."); }
  }
  useEffect(() => { setPreview(null); setJob(null); setMessage(""); void load(); return () => { generation.current += 1; }; }, [projectId, proposalId, actionId]);

  useEffect(() => {
    if (!job || !["queued", "running", "retrying"].includes(job.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const value = await api<unknown>(`/projects/${projectId}/storage-mutations/jobs/${job.job_id}`);
        setJob(parseJob(value, projectId));
      } catch { setMessage("Не удалось обновить безопасный статус."); }
    }, 750);
    return () => window.clearTimeout(timer);
  }, [job, projectId]);

  useEffect(() => {
    if (job?.status === "completed" && job.record_version) void load();
  }, [job?.status, job?.record_version]);

  async function submit(operation: "confirm" | "rollback") {
    if (!preview?.execution_allowed) return;
    setMessage("");
    const key = `storage-ui-${crypto.randomUUID()}`;
    try {
      const queued = parseQueuedMutation(await api<unknown>(`/projects/${projectId}/storage-mutations/${operation}`, {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify({ proposal_id: proposalId, action_id: actionId, record_version: preview.record_version }),
      }), projectId);
      setJob({ job_id: queued.job_id, project_id: projectId, status: queued.status, progress: 0, outcome: null, record_version: null });
    } catch (error) {
      setMessage(error instanceof ApiError && error.status === 409 ? "Версия изменилась. Обновите предпросмотр." : "Действие не поставлено в очередь.");
    }
  }

  if (!preview) return <div className="storage-mutation" role="status">{message || "Проверяю точную версию…"}</div>;
  const busy = !!job && ["queued", "running", "retrying"].includes(job.status);
  return <section className="storage-mutation" aria-label="Безопасное изменение файла">
    <div><strong>{preview.kind === "rename" ? "Переименование" : "Перемещение"}</strong>
      <span>{preview.before_name} → {preview.after_name}</span></div>
    <small>Версия {preview.record_version} · {preview.provider === "google_drive" ? "Google Drive" : "Яндекс Диск"}</small>
    {!preview.execution_allowed && <p>Исполнение выключено: доступен только синтетический тестовый контур.</p>}
    {message && <p role="alert">{message}</p>}
    {job && <p role="status">{job.status} · {job.progress}%{job.outcome ? ` · ${job.outcome}` : ""}</p>}
    <div><button type="button" disabled={!preview.execution_allowed || busy} onClick={() => void submit("confirm")}>Подтвердить точное изменение</button>
      <button type="button" disabled={!preview.execution_allowed || !preview.can_rollback || busy} onClick={() => void submit("rollback")}>Явно откатить</button>
      <button type="button" onClick={() => void load()}>Обновить</button></div>
  </section>;
}
