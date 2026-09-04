import { api } from "../../api/client";
import type { DraftInput, MailAddress, MailAssistRequest, MailAssistResult, MailCapabilities, MailDraft, MailFolder, MailFolderKind, MailMessage, MailSettings, MailThread } from "./types";

type RawDraft = Omit<MailDraft, "to" | "cc" | "bcc" | "safe_error" | "receipt" | "message_id"> & {
  to: string[]; cc: string[]; bcc: string[]; error_code?: string | null;
  reply_to_message_id?: number | null;
  receipt?: { external_message_id?: string | null; sent_at?: string | null } | null;
};
type RawMessage = {
  id: number; project_id: number; contract_id?: number | null; direction: "incoming" | "outgoing";
  thread_id: string; subject: string; sender?: string | null; content: string; summary?: string;
  headers?: { to?: string; cc?: string }; attachments?: MailMessage["attachments"];
  status: string; context_confirmed?: boolean; created_at: string; drafts?: RawDraft[];
};

function addresses(value: string | string[] | undefined | null): MailAddress[] {
  const values = Array.isArray(value) ? value : (value || "").split(/[;,]/);
  return values.map((email) => email.trim()).filter(Boolean).map((email) => ({ email }));
}

function safeDraftError(code?: string | null): string | null {
  if (!code) return null;
  if (code === "provider_rejected_before_effect") return "Провайдер отклонил отправку; письмо не отправлено.";
  if (code === "provider_outcome_unknown") return "Результат у провайдера неизвестен. Автоматический повтор заблокирован.";
  return "Не удалось завершить отправку. Код ошибки доступен в журнале.";
}

function plainPreview(value: string): string {
  if (typeof DOMParser === "undefined" || !/<[a-z][\s\S]*>/i.test(value)) {
    return value.replace(/\s+/g, " ").trim().slice(0, 180);
  }
  const parsed = new DOMParser().parseFromString(value, "text/html");
  parsed.querySelectorAll("script, style, noscript, iframe, object, embed, svg").forEach((node) => node.remove());
  return (parsed.body.textContent || "").replace(/\s+/g, " ").trim().slice(0, 180);
}

function normalizeDraft(raw: RawDraft): MailDraft {
  return {
    ...raw, message_id: raw.reply_to_message_id || null,
    to: addresses(raw.to), cc: addresses(raw.cc), bcc: addresses(raw.bcc),
    safe_error: safeDraftError(raw.error_code),
    receipt: raw.receipt ? {
      provider_message_id: raw.receipt.external_message_id || null,
      sent_at: raw.receipt.sent_at || null,
    } : null,
  };
}

function message(raw: RawMessage): MailMessage {
  const sender = raw.sender || "Отправитель не указан";
  return {
    id: raw.id, project_id: raw.project_id, contract_id: raw.contract_id,
    thread_id: raw.thread_id, direction: raw.direction, sender: { email: sender },
    to: addresses(raw.headers?.to), cc: addresses(raw.headers?.cc), subject: raw.subject,
    preview: raw.summary || plainPreview(raw.content), content: raw.content,
    summary: raw.summary, received_at: raw.created_at, status: raw.status,
    needs_attention: !raw.context_confirmed || ["ready", "in_progress"].includes(raw.status),
    context_confirmed: raw.context_confirmed, attachments: raw.attachments || [],
    drafts: (raw.drafts || []).map(normalizeDraft),
  };
}

function thread(raw: { thread_id: string; messages: RawMessage[] }): MailThread {
  const messages = raw.messages.map(message);
  const latest = messages[messages.length - 1];
  const participants = Array.from(new Map(
    messages.flatMap((row) => [row.sender, ...row.to]).map((row) => [row.email, row]),
  ).values());
  return {
    id: raw.thread_id, subject: latest?.subject || "Без темы", participants,
    last_message_at: latest?.received_at || new Date(0).toISOString(),
    unread_count: messages.filter((row) => row.status === "ready").length,
    needs_attention: messages.some((row) => row.needs_attention), messages,
  };
}

function queryString(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return params.toString();
}

export const mailClientApi = {
  capabilities(projectId: number) {
    return api<{ provider: string; connected: boolean; features: Record<string, boolean> }>(`/mail/projects/${projectId}/capabilities`).then((raw) => ({
      provider: raw.provider,
      connected: raw.connected,
      can_send: raw.features.compose,
      can_compose: raw.features.compose,
      can_reply: raw.features.reply,
      can_reply_all: raw.features.reply_all,
      can_forward: raw.features.forward,
      can_attach: raw.features.attachment_send,
      supports_threads: raw.features.threads,
      versioned_approval: raw.features.explicit_revision_approval,
    } satisfies MailCapabilities));
  },
  folders(projectId: number) {
    return api<{ folders: Array<{ id: string; name: string; count?: number }> }>(`/mail/projects/${projectId}/folders`).then((raw) => ({
      folders: raw.folders
        .filter((item): item is { id: MailFolderKind; name: string; count?: number } => ["inbox", "attention", "drafts", "sent", "archive", "spam", "trash", "all"].includes(item.id))
        .map((item) => ({ kind: item.id, label: item.name, count: item.count } satisfies MailFolder)),
    }));
  },
  messages(projectId: number, folder: MailFolderKind, query = "") {
    const params = queryString({ folder, query, limit: 200 });
    return api<{ messages: RawMessage[]; next_cursor?: string | null }>(`/mail/projects/${projectId}/messages?${params}`)
      .then((raw) => ({ items: raw.messages.map(message), next_cursor: raw.next_cursor }));
  },
  async threads(projectId: number, folder: MailFolderKind, query = "") {
    if (folder === "drafts") {
      const raw = await api<{ drafts: RawDraft[] }>(`/mail/projects/${projectId}/drafts`);
      const needle = query.trim().toLocaleLowerCase("ru-RU");
      const items = raw.drafts.map(normalizeDraft).filter((item) => !needle || `${item.subject}\n${item.body}\n${item.to.map((row) => row.email).join(" ")}`.toLocaleLowerCase("ru-RU").includes(needle)).map((item) => {
        const date = item.updated_at || item.created_at || "1970-01-01T00:00:00Z";
        const message: MailMessage = {
          id: item.message_id || -item.id, project_id: item.project_id, contract_id: item.contract_id,
          thread_id: `draft:${item.id}`, direction: "outgoing", sender: { email: "Я" }, to: item.to,
          cc: item.cc, subject: item.subject, preview: plainPreview(item.body),
          content: item.body, received_at: date, status: item.status, needs_attention: item.status !== "sent",
          context_confirmed: true, attachments: item.attachments, drafts: [item],
        };
        return {
          id: message.thread_id, subject: item.subject, participants: item.to,
          last_message_at: date, unread_count: 0, needs_attention: message.needs_attention || false,
          messages: [message],
        } satisfies MailThread;
      });
      return { items, next_cursor: null };
    }
    const params = queryString({ folder, query, limit: 200 });
    return api<{ threads: Array<{ thread_id: string; latest: RawMessage }>; next_cursor?: string | null }>(`/mail/projects/${projectId}/threads?${params}`)
      .then((raw) => ({ items: raw.threads.map((row) => thread({ thread_id: row.thread_id, messages: [row.latest] })), next_cursor: raw.next_cursor }));
  },
  thread(projectId: number, threadId: string) {
    return api<{ thread_id: string; messages: RawMessage[] }>(`/mail/projects/${projectId}/threads/${encodeURIComponent(threadId)}`).then(thread);
  },
  message(_projectId: number, messageId: number) {
    return api<RawMessage>(`/mail/messages/${messageId}`).then(message);
  },
  createDraft(input: DraftInput) {
    return api<RawDraft>("/mail/drafts", { method: "POST", body: JSON.stringify(input) }).then(normalizeDraft);
  },
  updateDraft(draftId: number, draft: MailDraft) {
    return api<RawDraft>(`/mail/drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: draft.revision,
        contract_id: draft.contract_id ?? null,
        to: draft.to.map((row) => row.email),
        cc: draft.cc.map((row) => row.email),
        bcc: draft.bcc.map((row) => row.email),
        subject: draft.subject,
        body: draft.body,
        body_format: draft.body_format || "html",
      }),
    }).then(normalizeDraft);
  },
  approveDraft(draftId: number, revision: number) {
    return api<RawDraft>(`/mail/drafts/${draftId}/approve`, {
      method: "POST",
      body: JSON.stringify({ revision }),
    }).then(normalizeDraft);
  },
  sendDraft(draftId: number, revision: number, idempotencyKey: string) {
    return api<RawDraft>(`/mail/drafts/${draftId}/send`, {
      method: "POST",
      body: JSON.stringify({ revision, idempotency_key: idempotencyKey }),
    }).then(normalizeDraft);
  },
  settings() {
    return api<MailSettings>("/mail/settings");
  },
  updateSettings(settings: MailSettings) {
    return api<MailSettings>("/mail/settings", { method: "PUT", body: JSON.stringify(settings) });
  },
  assist(input: MailAssistRequest) {
    return api<MailAssistResult>("/mail/assist", { method: "POST", body: JSON.stringify(input) });
  },
  confirmContext(messageId: number, projectId: number, contractId: number | null) {
    return api<RawMessage>(`/ai-secretary/inbox/${messageId}/confirm-context`, {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, contract_id: contractId }),
    }).then(message);
  },
  setMessageStatus(messageId: number, status: "in_progress" | "completed") {
    return api<RawMessage>(`/ai-secretary/inbox/${messageId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }).then(message);
  },
  moveMessage(messageId: number, destination: "archive" | "spam" | "trash" | "inbox") {
    return api<RawMessage>(`/mail/messages/${messageId}/move`, {
      method: "POST", body: JSON.stringify({ destination }),
    }).then(message);
  },
};
