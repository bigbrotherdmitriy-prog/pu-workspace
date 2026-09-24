import { api } from "../../api/client";
import { awaitLocalUploadJobs, localUploadMimeType } from "../documents/localUploadJobs";

export type InvoiceUploadResult = { name: string; documentIds: number[]; error?: string };
export const invoiceFileSupported = (file: File) => file.type === "application/pdf" || /\.pdf$/i.test(file.name);

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Не удалось прочитать ${file.name}`));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(file);
  });
}

export async function uploadInvoiceBatch(
  projectId: number, files: File[], onProgress: (message: string) => void,
): Promise<InvoiceUploadResult[]> {
  if (!projectId) throw new Error("Сначала выберите проект.");
  const results: InvoiceUploadResult[] = [];
  // One file per request bounds base64 memory and keeps individual failures visible.
  for (const [index, file] of files.entries()) {
    const name = file.webkitRelativePath || file.name;
    const prefix = `Счёт ${index + 1} из ${files.length}: ${name}`;
    onProgress(`${prefix} — загрузка`);
    try {
      const uploaded = await api("/local-upload/analyze", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, files: [{
          path: name, mime_type: localUploadMimeType(file), content_base64: await readBase64(file),
        }] }),
      });
      const completed = await awaitLocalUploadJobs(projectId, uploaded.jobs || [], (message) => onProgress(`${prefix} — ${message}`));
      if (!completed.documents.length) throw new Error("Текст не извлечён. Проверьте документ в разделе «Документы».");
      results.push({ name, documentIds: completed.documents });
    } catch (error) {
      results.push({ name, documentIds: [], error: (error as Error).message });
    }
  }
  return results;
}
