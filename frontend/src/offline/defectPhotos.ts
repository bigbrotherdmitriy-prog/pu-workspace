/** Explicit bounded offline photos; no compression, external upload, or fake camera data. */
import { api } from "../api/client";
import { captureOfflineSession, requireOfflineSession } from "./lifecycle";
export type DefectPhotoIntent = Readonly<{ operationKey: string; name: string; mime: string; kind: "before" | "after"; sha256: string; blob: Blob }>;
export const MAX_OFFLINE_PHOTOS = 12;
export const MAX_OFFLINE_PHOTO_BYTES = 10 * 1024 * 1024;
export const MAX_OFFLINE_PHOTO_TOTAL_BYTES = 20 * 1024 * 1024;
const mimeTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
function bytes(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === "function") return blob.arrayBuffer();
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result as ArrayBuffer); reader.onerror = () => reject(reader.error); reader.readAsArrayBuffer(blob); });
}
export async function photoDigest(blob: Blob): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error("Для безопасного сохранения фото нужен защищённый браузер с WebCrypto");
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await bytes(blob))), byte => byte.toString(16).padStart(2, "0")).join("");
}
export async function checkDefectPhotos(value: unknown): Promise<readonly DefectPhotoIntent[]> {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_OFFLINE_PHOTOS) throw new Error("Не более 12 фото на замечание");
  let total = 0; const keys = new Set<string>(); const result: DefectPhotoIntent[] = [];
  for (const row of value) {
    if (!row || typeof row !== "object" || !(row.blob instanceof Blob) || row.blob.size <= 0 || row.blob.size > MAX_OFFLINE_PHOTO_BYTES
      || typeof row.operationKey !== "string" || !/^[A-Za-z0-9_.:-]{8,160}$/.test(row.operationKey) || keys.has(row.operationKey)
      || typeof row.name !== "string" || !row.name.trim() || Array.from(row.name).length > 300
      || !mimeTypes.has(row.mime) || (row.kind !== "before" && row.kind !== "after") || !/^[a-f0-9]{64}$/.test(row.sha256)) throw new Error("Повреждены метаданные фото или превышен лимит 10 МБ");
    total += row.blob.size; if (total > MAX_OFFLINE_PHOTO_TOTAL_BYTES) throw new Error("Все фото одного замечания: не более 20 МБ; уменьшите выбор без потери исходников");
    if (await photoDigest(row.blob) !== row.sha256) throw new Error("Фото отличается от сохранённого отпечатка; отправка остановлена");
    keys.add(row.operationKey); result.push(Object.freeze({ operationKey: row.operationKey, name: row.name, mime: row.mime, kind: row.kind, sha256: row.sha256, blob: row.blob }));
  }
  return Object.freeze(result);
}
export async function prepareDefectPhotos(files: File[], kind: "before" | "after" = "before"): Promise<readonly DefectPhotoIntent[]> {
  if (!globalThis.crypto?.randomUUID || !crypto.subtle) throw new Error("Для фото нужен защищённый браузер с WebCrypto");
  // Check complete selection before reading/copying any content.
  if (files.length > MAX_OFFLINE_PHOTOS || files.some(file => !file.size || file.size > MAX_OFFLINE_PHOTO_BYTES || !mimeTypes.has(file.type) || !file.name.trim() || Array.from(file.name).length > 300)
    || files.reduce((sum, file) => sum + file.size, 0) > MAX_OFFLINE_PHOTO_TOTAL_BYTES) throw new Error("Фото: JPG/PNG/WEBP, до 12 файлов, до 10 МБ каждый и 20 МБ всего");
  const result: DefectPhotoIntent[] = [];
  for (const file of files) result.push({ operationKey: crypto.randomUUID(), name: file.name, mime: file.type, kind, sha256: await photoDigest(file), blob: file.slice(0, file.size, file.type) });
  return checkDefectPhotos(result);
}
export function photoIdentity(photos: readonly DefectPhotoIntent[]) { return photos.map(({ blob, ...metadata }) => ({ ...metadata, size: blob.size })); }
export async function sendDefectPhotos(userId: number, projectId: number, defectId: number, photos: readonly DefectPhotoIntent[]): Promise<void> {
  const session = captureOfflineSession(userId);
  const checked = await checkDefectPhotos(photos); requireOfflineSession(session);
  for (const photo of checked) {
    const result = await api<Record<string, unknown>>(`/projects/${projectId}/defects/${defectId}/photos/idempotent?name=${encodeURIComponent(photo.name)}&kind=${photo.kind}`, {
      method: "POST", body: photo.blob, headers: { "Content-Type": photo.mime, "X-PU-Expected-User": String(userId), "X-PU-Photo-Operation": photo.operationKey },
    });
    requireOfflineSession(session);
    if (result?.status !== "applied" || typeof result.receipt_id !== "number" || !Number.isSafeInteger(result.receipt_id) || result.receipt_id <= 0
      || typeof result.photo_id !== "number" || !Number.isSafeInteger(result.photo_id) || result.photo_id <= 0
      || result.project_id !== projectId || result.defect_id !== defectId || result.user_id !== userId || result.operation_key !== photo.operationKey
      || result.sha256 !== photo.sha256 || result.size_bytes !== photo.blob.size || result.kind !== photo.kind || result.filename !== photo.name || result.mime_type !== photo.mime) throw new Error("Доставка исходного фото не подтверждена; локальные фото сохранены для повтора");
  }
}
