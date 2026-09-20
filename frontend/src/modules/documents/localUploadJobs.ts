import { api } from "../../api/client";

export type QueuedLocalUploadJob = { job_id: number; status: string };

type LocalUploadJob = {
  job_id: number;
  status: string;
  progress: number;
  error?: string | null;
  result?: {
    processed: number;
    skipped: number;
    tasks: number;
    risks: number;
    decisions: number;
    drafts: number;
    documents: number[];
  } | null;
};

export type LocalUploadSummary = {
  processed: number;
  skipped: number;
  tasks: number;
  risks: number;
  decisions: number;
  drafts: number;
  documents: number[];
};

const POLL_INTERVAL_MS = 1000;
const POLL_LIMIT = 300;

const MIME_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  csv: "text/csv",
  md: "text/markdown",
  txt: "text/plain",
  bmp: "image/bmp",
  jpeg: "image/jpeg",
  jpg: "image/jpeg",
  png: "image/png",
  tif: "image/tiff",
  tiff: "image/tiff",
  webp: "image/webp",
};

const SUPPORTED_LOCAL_UPLOAD_MIME_TYPES = new Set(Object.values(MIME_BY_EXTENSION));

export function localUploadMimeType(file: File): string {
  const browserMimeType = file.type.toLowerCase();
  if (SUPPORTED_LOCAL_UPLOAD_MIME_TYPES.has(browserMimeType)) return browserMimeType;
  const extension = file.name.toLowerCase().split(".").pop() || "";
  return MIME_BY_EXTENSION[extension] || "application/octet-stream";
}

export function isSupportedLocalUploadFile(file: File): boolean {
  return SUPPORTED_LOCAL_UPLOAD_MIME_TYPES.has(localUploadMimeType(file));
}

async function waitForJob(
  projectId: number,
  jobId: number,
  onProgress?: (message: string) => void,
  position = 1,
  total = 1,
): Promise<LocalUploadJob> {
  for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
    onProgress?.(`Анализ ${position} из ${total}`);
    const job = await api<LocalUploadJob>(`/local-upload/projects/${projectId}/jobs/${jobId}`);
    if (job.status === "completed") return job;
    if (["failed", "dead_letter", "cancelled"].includes(job.status)) {
      throw new Error(job.error || `Обработка файла завершилась со статусом ${job.status}`);
    }
    await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
  }
  throw new Error("Анализ документов не завершился вовремя. Его состояние сохранено; обновите страницу позже.");
}

export async function awaitLocalUploadJobs(
  projectId: number,
  jobs: QueuedLocalUploadJob[],
  onProgress?: (message: string) => void,
): Promise<LocalUploadSummary> {
  if (!jobs.length) throw new Error("Сервер не вернул задания защищённой обработки");
  const total: LocalUploadSummary = {
    processed: 0, skipped: 0, tasks: 0, risks: 0,
    decisions: 0, drafts: 0, documents: [],
  };
  for (let index = 0; index < jobs.length; index += 1) {
    const job = await waitForJob(projectId, jobs[index].job_id, onProgress, index + 1, jobs.length);
    if (!job.result) throw new Error("Обработка завершилась без безопасного результата");
    total.processed += job.result.processed;
    total.skipped += job.result.skipped;
    total.tasks += job.result.tasks;
    total.risks += job.result.risks;
    total.decisions += job.result.decisions;
    total.drafts += job.result.drafts;
    total.documents.push(...job.result.documents);
  }
  total.documents = [...new Set(total.documents)];
  return total;
}
