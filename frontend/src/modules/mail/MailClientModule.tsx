import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, Archive, Bot, CheckCircle2, ChevronRight,
  Clock3, FileText, Inbox, Mail, MailOpen, Paperclip, PenLine, RefreshCw,
  Reply, ReplyAll, Search, Send, Settings, ShieldAlert, Sparkles, Trash2, Undo2, XCircle,
} from "lucide-react";
import { ApiError } from "../../api/client";
import { mailClientApi } from "./mailClientApi";
import type {
  DraftInput, MailAddress, MailCapabilities, MailContract, MailDraft, MailFolder,
  MailFolderKind, MailMessage, MailProject, MailSettings, MailThread,
} from "./types";
import { MailSettingsDialog } from "./MailSettingsDialog";
import { editorHtml, editorPlainText, RichTextEditor } from "./RichTextEditor";
import "./mail-client.css";

type Props = {
  projectId: number;
  currentUserEmail?: string;
  projects: MailProject[];
  contracts: MailContract[];
  syncing: boolean;
  syncStatus?: string;
  onSync: () => Promise<void> | void;
  onOpenContacts: () => void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  client?: typeof mailClientApi;
};

type ComposerState = {
  draft: MailDraft | null;
  mode: MailDraft["mode"];
  replyTo: MailMessage | null;
  projectId: number;
  contractId: number;
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
  dirty: boolean;
};

const defaultMailSettings: MailSettings = {
  display_name: "", signature_html: "", auto_signature_new: true, auto_signature_reply: true,
  default_font: "Arial", default_font_size: "14px", default_text_color: "#18211d",
};

const emptyCapabilities: MailCapabilities = {
  provider: "mail", connected: false, can_send: false, can_compose: false,
  can_reply: false, can_reply_all: false, can_forward: false, can_attach: false,
  can_move: false,
  supports_threads: false, versioned_approval: false,
};

const defaultFolders: MailFolder[] = [
  { kind: "inbox", label: "Входящие" },
  { kind: "attention", label: "Требуют внимания" },
  { kind: "drafts", label: "Черновики" },
  { kind: "sent", label: "Отправленные" },
  { kind: "archive", label: "Архив" },
  { kind: "spam", label: "Спам" },
  { kind: "trash", label: "Удалённые" },
  { kind: "all", label: "Вся почта" },
];

const folderIcons = {
  inbox: Inbox, attention: AlertTriangle, drafts: FileText, sent: Send, archive: Archive,
  spam: ShieldAlert, trash: Trash2, all: MailOpen,
};

function addressText(addresses: MailAddress[]): string {
  return addresses.map((item) => item.email).join(", ");
}

function parseAddresses(value: string): string[] {
  return Array.from(new Set(value.split(/[;,\n]/).map((item) => item.trim()).filter(Boolean)));
}

function replySubject(subject: string): string {
  return /^re:/i.test(subject) ? subject : `Re: ${subject}`;
}

function forwardSubject(subject: string): string {
  return /^fwd:/i.test(subject) ? subject : `Fwd: ${subject}`;
}

export function readableMessageBody(content: string): string {
  let source = content.trim();
  const legacyCssStart = source.search(/(?:^|\s)(?:html|body|table|td|p|\.[a-z_-][\w-]*)\s*\{/i);
  if (legacyCssStart >= 0 && /(?:!important|-webkit-|@media|font-size\s*:|color\s*:|margin\s*:)/i.test(source)) {
    const legacyCssEnd = source.lastIndexOf("}");
    const prefix = source.slice(0, legacyCssStart).trim();
    const readableTail = source.slice(legacyCssEnd + 1).trim();
    if (legacyCssEnd > legacyCssStart && readableTail.length >= 12) {
      source = [prefix, readableTail].filter(Boolean).join("\n");
    }
  }
  if (!source || !/<[a-z][\s\S]*>/i.test(source) || typeof DOMParser === "undefined") return source;
  const parsed = new DOMParser().parseFromString(source, "text/html");
  parsed.querySelectorAll("script, style, noscript, iframe, object, embed, svg").forEach((node) => node.remove());
  parsed.querySelectorAll("br").forEach((node) => node.replaceWith(parsed.createTextNode("\n")));
  parsed.querySelectorAll("p, div, li, tr, h1, h2, h3, h4, h5, h6, blockquote").forEach((node) => {
    node.append(parsed.createTextNode("\n"));
  });
  return (parsed.body.textContent || "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function statusLabel(status: MailDraft["status"]): string {
  return {
    draft: "Черновик — не отправлен",
    approved: "Подтверждён — не отправлен",
    rejected: "Черновик отклонён",
    queued: "Ожидает отправки",
    sending: "Отправляется",
    sent: "Отправлено",
    failed: "Ошибка без отправки",
    unknown: "Результат отправки неизвестен",
  }[status];
}

function statusIcon(status: MailDraft["status"]) {
  if (status === "sent") return <CheckCircle2 />;
  if (status === "rejected" || status === "failed" || status === "unknown") return <XCircle />;
  return <Clock3 />;
}

function initialComposer(projectId: number): ComposerState {
  return {
    draft: null, mode: "compose", replyTo: null, projectId, contractId: 0,
    to: "", cc: "", bcc: "", subject: "", body: "", dirty: false,
  };
}

function composeBody(content: string, settings: MailSettings, includeSignature: boolean): string {
  const body = editorHtml(content, settings.default_font, settings.default_font_size, settings.default_text_color);
  if (!includeSignature || !editorPlainText(settings.signature_html)) return body;
  return `${body}<div><br></div><div class="pu-mail-signature">--<br>${settings.signature_html}</div>`;
}

function bodyForAi(value: string, settings: MailSettings): string {
  const content = editorPlainText(value);
  const signature = editorPlainText(settings.signature_html);
  if (!signature) return content;
  const suffix = `--\n${signature}`;
  return content.endsWith(suffix) ? content.slice(0, -suffix.length).trimEnd() : content;
}

export function MailClientModule({
  projectId, currentUserEmail, projects, contracts, syncing, syncStatus, onSync, onOpenContacts,
  onNotice, onError, client = mailClientApi,
}: Props) {
  const [folder, setFolder] = useState<MailFolderKind>("inbox");
  const [folders, setFolders] = useState<MailFolder[]>(defaultFolders);
  const [capabilities, setCapabilities] = useState<MailCapabilities>(emptyCapabilities);
  const [threads, setThreads] = useState<MailThread[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [safeError, setSafeError] = useState("");
  const [composer, setComposer] = useState<ComposerState | null>(null);
  const [showCopy, setShowCopy] = useState(false);
  const [busy, setBusy] = useState("");
  const [confirmSend, setConfirmSend] = useState(false);
  const [mailSettings, setMailSettings] = useState<MailSettings>(defaultMailSettings);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [showAiAssist, setShowAiAssist] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiTone, setAiTone] = useState<"business" | "neutral" | "friendly">("business");
  const [aiNotes, setAiNotes] = useState("");
  const requestRef = useRef(0);
  const subjectRef = useRef<HTMLInputElement>(null);

  async function loadMailbox(nextFolder = folder, nextQuery = query) {
    const requestId = ++requestRef.current;
    setLoading(true);
    setSafeError("");
    try {
      const [capabilityResult, folderResult, threadResult] = await Promise.all([
        client.capabilities(projectId),
        client.folders(projectId),
        client.threads(projectId, nextFolder, nextQuery),
      ]);
      if (requestId !== requestRef.current) return;
      setCapabilities(capabilityResult);
      setFolders(folderResult.folders);
      const nextThreads = threadResult.items;
      setThreads(nextThreads);
      setSelectedThreadId((current) => nextThreads.some((row) => row.id === current)
        ? current : nextThreads[0]?.id || null);
    } catch (error) {
      if (requestId !== requestRef.current) return;
      const message = (error as Error).message;
      setSafeError(message);
      setThreads([]);
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    setFolder("inbox");
    setQuery("");
    setComposer(null);
    setSelectedThreadId(null);
    void loadMailbox("inbox", "");
    return () => { ++requestRef.current; };
    // Loading is deliberately keyed only to the authoritative project selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    void client.settings().then(setMailSettings).catch(() => {
      setMailSettings(defaultMailSettings);
    });
  }, [client]);

  useEffect(() => {
    if (!selectedThreadId || folder === "drafts") return;
    const requestId = requestRef.current;
    void client.thread(projectId, selectedThreadId).then((loaded) => {
      if (requestId !== requestRef.current) return;
      setThreads((current) => current.map((row) => row.id === loaded.id ? loaded : row));
    }).catch(() => {
      // The list preview remains useful; explicit mailbox reload exposes provider failures.
    });
  }, [client, folder, projectId, selectedThreadId]);

  useEffect(() => {
    if (composer) window.setTimeout(() => subjectRef.current?.focus(), 0);
  }, [composer?.mode]);

  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const editing = target?.matches("input, textarea, select, [contenteditable=true]");
      if (event.key === "Escape") {
        if (showSettings) setShowSettings(false);
        else if (confirmSend) setConfirmSend(false);
        else if (composer) setComposer(null);
      } else if (!editing && event.key.toLocaleLowerCase("ru-RU") === "c" && capabilities.can_compose) {
        event.preventDefault();
        openComposer("compose");
      }
    }
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  });

  const selectedThread = useMemo(
    () => threads.find((row) => row.id === selectedThreadId) || null,
    [threads, selectedThreadId],
  );
  const selectedMessage = selectedThread?.messages[selectedThread.messages.length - 1] || null;

  function openComposer(mode: MailDraft["mode"], message: MailMessage | null = selectedMessage) {
    setShowAiAssist(false);
    setAiNotes("");
    setAiInstruction("");
    if (mode === "compose") {
      setComposer({
        ...initialComposer(projectId),
        body: composeBody("", mailSettings, mailSettings.auto_signature_new),
      });
      return;
    }
    if (!message) return;
    const ownAddress = (currentUserEmail || "").trim().toLocaleLowerCase();
    const isOwn = (value: string) => {
      const bracket = value.match(/<([^>]+)>/);
      return (bracket?.[1] || value).trim().toLocaleLowerCase() === ownAddress;
    };
    const recipients = mode === "reply"
      ? [message.sender.email]
      : mode === "reply_all"
        ? Array.from(new Set([message.sender.email, ...message.to.map((row) => row.email)]))
          .filter((value) => !isOwn(value))
        : [];
    const copies = mode === "reply_all"
      ? Array.from(new Set(message.cc.map((row) => row.email)))
        .filter((value) => !isOwn(value) && !recipients.includes(value))
      : [];
    setComposer({
      ...initialComposer(projectId), mode, replyTo: message,
      contractId: message.contract_id || 0,
      to: recipients.join(", "),
      cc: copies.join(", "),
      subject: mode === "forward" ? forwardSubject(message.subject) : replySubject(message.subject),
      body: composeBody(
        mode === "forward"
          ? `\n\n---------- Пересылаемое сообщение ----------\nОт: ${message.sender.email}\nТема: ${message.subject}\n\n${message.content}`
          : "",
        mailSettings,
        mailSettings.auto_signature_reply,
      ),
    });
  }

  function updateComposer(patch: Partial<ComposerState>) {
    setComposer((current) => current ? { ...current, ...patch, dirty: true } : current);
  }

  function composerDraft(current: ComposerState): MailDraft {
    if (!current.draft) throw new Error("Черновик ещё не сохранён");
    return {
      ...current.draft,
      project_id: current.projectId,
      contract_id: current.contractId || null,
      to: parseAddresses(current.to).map((email) => ({ email })),
      cc: parseAddresses(current.cc).map((email) => ({ email })),
      bcc: parseAddresses(current.bcc).map((email) => ({ email })),
      subject: current.subject.trim(), body: current.body.trim(), body_format: "html",
      status: current.dirty ? "draft" : current.draft.status,
    };
  }

  function draftInput(current: ComposerState): DraftInput {
    return {
      project_id: current.projectId,
      contract_id: current.contractId || null,
      mode: current.mode,
      reply_to_message_id: current.replyTo?.id || null,
      to: parseAddresses(current.to), cc: parseAddresses(current.cc), bcc: parseAddresses(current.bcc),
      subject: current.subject.trim(), body: current.body.trim(), body_format: "html", attachments: [],
    };
  }

  function validateComposer(current: ComposerState): string {
    if (!parseAddresses(current.to).length) return "Добавьте хотя бы одного получателя";
    if (!current.subject.trim()) return "Укажите тему письма";
    if (!editorPlainText(current.body)) return "Введите текст письма";
    return "";
  }

  async function saveDraft() {
    if (!composer) return;
    const validation = validateComposer(composer);
    if (validation) { onError(validation); return; }
    setBusy("save");
    try {
      const saved = composer.draft
        ? await client.updateDraft(composer.draft.id, composerDraft(composer))
        : await client.createDraft(draftInput(composer));
      setComposer((current) => current ? { ...current, draft: saved, dirty: false } : current);
      onNotice(`Черновик версии ${saved.revision} сохранён и не отправлен`);
      await loadMailbox();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        onError("Черновик уже изменён в другой вкладке. Закройте редактор и откройте письмо заново.");
      } else onError((error as Error).message);
    } finally { setBusy(""); }
  }

  async function approveDraft() {
    if (!composer?.draft || composer.dirty) return;
    setBusy("approve");
    try {
      const approved = await client.approveDraft(composer.draft.id, composer.draft.revision);
      setComposer((current) => current ? { ...current, draft: approved, dirty: false } : current);
      onNotice(`Подтверждена версия ${approved.approved_revision ?? approved.revision}. Письмо ещё не отправлено.`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        onError("Версия черновика изменилась. Проверьте актуальный текст и подтвердите его заново.");
      } else onError((error as Error).message);
    } finally { setBusy(""); }
  }

  async function sendDraft() {
    if (!composer?.draft || composer.dirty) return;
    const draft = composerDraft(composer);
    if (draft.approved_revision !== draft.revision) return;
    setBusy("send");
    setConfirmSend(false);
    try {
      const sent = await client.sendDraft(draft.id, draft.revision, crypto.randomUUID());
      setComposer((current) => current ? { ...current, draft: sent, dirty: false } : current);
      onNotice(statusLabel(sent.status));
      await loadMailbox("sent", "");
      if (sent.status === "sent") {
        setFolder("sent");
        setComposer(null);
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        onError("Отправка остановлена: подтверждена другая версия или состояние письма изменилось.");
      } else onError((error as Error).message);
    } finally { setBusy(""); }
  }

  async function saveMailSettings(next: MailSettings) {
    setSettingsBusy(true);
    try {
      const saved = await client.updateSettings(next);
      setMailSettings(saved);
      setShowSettings(false);
      onNotice("Настройки почты и подпись сохранены");
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setSettingsBusy(false);
    }
  }

  async function runAiAssist(action: "compose" | "reply" | "improve" | "shorten" | "formal" | "friendly") {
    if (!composer) return;
    if (action === "compose" && !aiInstruction.trim() && !editorPlainText(composer.body)) {
      onError("Опишите Gemini, какое письмо нужно подготовить");
      return;
    }
    setBusy("ai");
    setAiNotes("");
    try {
      const result = await client.assist({
        project_id: composer.projectId,
        reply_to_message_id: composer.replyTo?.id || null,
        action,
        tone: aiTone,
        instruction: aiInstruction.trim(),
        subject: composer.subject,
        body: bodyForAi(composer.body, mailSettings),
      });
      const includeSignature = composer.mode === "compose" ? mailSettings.auto_signature_new : mailSettings.auto_signature_reply;
      setComposer((current) => current ? {
        ...current,
        subject: result.subject || current.subject,
        body: composeBody(result.body, mailSettings, includeSignature),
        dirty: true,
      } : current);
      setAiNotes(`${result.provider} · ${result.model}: ${result.notes}`);
      onNotice("Gemini подготовил вариант. Проверьте текст, сохраните и подтвердите версию.");
    } catch (error) {
      const message = (error as Error).message;
      onError(message.includes("external_ai_blocked")
        ? "Внешний AI запрещён политикой этого проекта"
        : message.includes("temporarily_unavailable") || message.includes("not_configured")
          ? "Gemini сейчас недоступен. Текущий текст черновика сохранён без изменений."
          : message);
    } finally {
      setBusy("");
    }
  }

  async function confirmContext(message: MailMessage, targetProjectId: number, contractId: number) {
    try {
      await client.confirmContext(message.id, targetProjectId, contractId || null);
      onNotice("Проект и договор письма подтверждены");
      await loadMailbox();
    } catch (error) { onError((error as Error).message); }
  }

  async function setMessageStatus(message: MailMessage) {
    try {
      await client.setMessageStatus(message.id, message.status === "in_progress" ? "completed" : "in_progress");
      await loadMailbox();
    } catch (error) { onError((error as Error).message); }
  }

  async function moveMessage(message: MailMessage, destination: "archive" | "spam" | "trash" | "inbox") {
    if (destination === "trash" && !window.confirm("Переместить письмо в корзину Gmail?")) return;
    if (destination === "spam" && !window.confirm("Пометить письмо как спам в Gmail?")) return;
    try {
      await client.moveMessage(message.id, destination);
      const labels = { archive: "Письмо перемещено в архив", spam: "Письмо помечено как спам", trash: "Письмо перемещено в корзину", inbox: "Письмо возвращено во входящие" };
      onNotice(labels[destination]);
      await loadMailbox(folder, query);
    } catch (error) {
      const detail = (error as Error).message;
      onError(detail.includes("outcome_unknown")
        ? "Результат операции в Gmail неизвестен. Обновите почту перед повтором."
        : detail);
    }
  }

  async function switchFolder(kind: MailFolderKind) {
    setFolder(kind);
    setSelectedThreadId(null);
    await loadMailbox(kind, query);
  }

  async function syncAndReload() {
    await onSync();
    await loadMailbox(folder, query);
  }

  const activeFolders = defaultFolders.map((fallback) => folders.find((item) => item.kind === fallback.kind) || fallback);
  const approvedCurrent = Boolean(composer?.draft && !composer.dirty &&
    composer.draft.approved_revision === composer.draft.revision && composer.draft.status === "approved");

  return <section className="mail-client" aria-label="Почтовый клиент">
    <div className="mail-client-top">
      <header className="mail-client-header">
        <div>
          <span className="eyebrow">КОММУНИКАЦИОННЫЙ ЦЕНТР</span>
          <h2>Почта проекта</h2>
          <p>Письма, контекст и AI-помощь. Отправка — только после подтверждения точной версии.</p>
        </div>
        <div className="mail-client-header-actions">
          <span className={`mail-connection ${capabilities.connected ? "ready" : "offline"}`}>
            {capabilities.connected ? `${capabilities.provider} подключён` : "Почта не подключена"}
          </span>
          <button type="button" className="secondary" onClick={onOpenContacts}>Контакты</button>
          <button type="button" className="secondary" onClick={() => setShowSettings(true)}><Settings />Настройки</button>
          <button type="button" onClick={() => void syncAndReload()} disabled={syncing}>
            <RefreshCw className={syncing ? "spinning" : ""} /> {syncing ? "Получаю…" : "Получить новые"}
          </button>
        </div>
      </header>
      {syncStatus && <p className="mail-sync-status" aria-live="polite">{syncStatus}</p>}
    </div>
    {safeError && <div className="mail-safe-error" role="alert">
      <AlertTriangle /><div><strong>Почтовый интерфейс пока недоступен</strong><p>{safeError}</p><button onClick={() => void loadMailbox()}>Повторить</button></div>
    </div>}
    {!safeError && <div className="mail-layout">
      <aside className="mail-folders" aria-label="Папки почты">
        <button className="mail-compose-button" disabled={!capabilities.can_compose} onClick={() => openComposer("compose") }>
          <PenLine /> Написать
        </button>
        {!capabilities.can_compose && <small>Создание писем не поддерживается подключённым адаптером.</small>}
        {capabilities.connected && !capabilities.can_move && <small>Для архива, спама и корзины переподключите Google в разделе «Интеграции».</small>}
        <nav>
          {activeFolders.map((item) => {
            const Icon = folderIcons[item.kind];
            return <button key={item.kind} className={folder === item.kind ? "active" : ""} onClick={() => void switchFolder(item.kind)}>
              <Icon /><span>{item.label}</span>{item.count !== undefined && <b>{item.count}</b>}
            </button>;
          })}
        </nav>
      </aside>
      <section className="mail-thread-list" aria-label="Список писем">
        <label className="mail-search"><Search /><input value={query} placeholder="Поиск писем" onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Enter") void loadMailbox(folder, query);
        }} /><button aria-label="Найти" onClick={() => void loadMailbox(folder, query)}><ChevronRight /></button></label>
        {loading ? <div className="mail-loading" aria-live="polite"><RefreshCw className="spinning" /> Загружаю письма…</div> : threads.length ? threads.map((thread) => {
          const last = thread.messages[thread.messages.length - 1];
          return <button className={`mail-thread-row ${selectedThreadId === thread.id ? "selected" : ""}`} key={thread.id} onClick={() => setSelectedThreadId(thread.id)}>
            <span className="mail-avatar">{(last.sender.name || last.sender.email).slice(0, 1).toLocaleUpperCase("ru-RU")}</span>
            <span className="mail-thread-copy"><strong>{last.sender.name || last.sender.email}</strong><b>{thread.subject}</b><small>{last.preview || last.summary || readableMessageBody(last.content)}</small></span>
            <time>{new Date(thread.last_message_at).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" })}</time>
            {thread.unread_count > 0 && <span className="mail-unread">{thread.unread_count}</span>}
            {thread.needs_attention && <AlertTriangle className="mail-attention" aria-label="Требует внимания" />}
          </button>;
        }) : <div className="mail-empty"><MailOpen /><strong>В этой папке писем нет</strong><span>Смените папку или получите новые письма.</span></div>}
      </section>
      <section className="mail-reading-pane" aria-label="Просмотр переписки">
        {selectedThread && selectedMessage ? <>
          <div className="mail-reading-head">
            <div><span className="eyebrow">ЦЕПОЧКА · {selectedThread.messages.length}</span><h2>{selectedThread.subject}</h2><p>{selectedThread.participants.map((item) => item.name || item.email).join(", ")}</p></div>
            <div className="mail-reading-actions">
              <button disabled={!capabilities.can_reply} onClick={() => openComposer("reply")}><Reply />Ответить</button>
              <button disabled={!capabilities.can_reply_all} onClick={() => openComposer("reply_all")}><ReplyAll />Всем</button>
              <button disabled={!capabilities.can_forward} onClick={() => openComposer("forward")}><ChevronRight />Переслать</button>
              {selectedMessage.direction === "incoming" && capabilities.can_move && (folder === "spam" || folder === "trash"
                ? <button onClick={() => void moveMessage(selectedMessage, "inbox")}><Inbox />Во входящие</button>
                : <>
                  <button title="Убрать из входящих" onClick={() => void moveMessage(selectedMessage, "archive")}><Archive />Архив</button>
                  <button title="Пометить как спам" onClick={() => void moveMessage(selectedMessage, "spam")}><ShieldAlert />Спам</button>
                  <button className="danger" title="Переместить в корзину" onClick={() => void moveMessage(selectedMessage, "trash")}><Trash2 />Удалить</button>
                </>)}
            </div>
          </div>
          <div className="mail-context-strip">
            <label>Проект<select defaultValue={selectedMessage.project_id} id={`mail-project-${selectedMessage.id}`}>
              {projects.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
            </select></label>
            <label>Договор<select defaultValue={selectedMessage.contract_id || 0} id={`mail-contract-${selectedMessage.id}`}>
              <option value={0}>Без договора</option>
              {contracts.filter((item) => item.project_id === selectedMessage.project_id).map((item) => <option value={item.id} key={item.id}>{item.number} — {item.title}</option>)}
            </select></label>
            <button onClick={() => {
              const project = document.getElementById(`mail-project-${selectedMessage.id}`) as HTMLSelectElement;
              const contract = document.getElementById(`mail-contract-${selectedMessage.id}`) as HTMLSelectElement;
              void confirmContext(selectedMessage, Number(project.value), Number(contract.value));
            }}>{selectedMessage.context_confirmed ? "Изменить связь" : "Подтвердить связь"}</button>
            <small>{selectedMessage.context_evidence || "Контекст не определён"}{selectedMessage.context_confidence !== undefined ? ` · ${Math.round(selectedMessage.context_confidence * 100)}%` : ""}</small>
          </div>
          <div className="mail-thread-messages">
            {selectedThread.messages.map((message) => <article key={message.id} className={message.direction === "outgoing" ? "outgoing" : "incoming"}>
              <header><span className="mail-avatar">{(message.sender.name || message.sender.email).slice(0, 1).toUpperCase()}</span><div><strong>{message.sender.name || message.sender.email}</strong><small>Кому: {addressText(message.to) || "не указан"}{message.cc.length ? ` · Копия: ${addressText(message.cc)}` : ""}</small></div><time>{new Date(message.received_at).toLocaleString("ru-RU")}</time></header>
              {message.summary && <div className="mail-ai-summary"><Bot /><div><strong>AI-сводка</strong><p>{message.summary}</p><small>Вывод AI нужно сверять с исходным письмом.</small></div></div>}
              <details open={selectedThread.messages.length === 1}><summary>Текст письма</summary><pre>{readableMessageBody(message.content)}</pre></details>
              {message.attachments.length > 0 && <div className="mail-attachments"><strong><Paperclip /> Вложения</strong>{message.attachments.map((attachment) => <span key={attachment.id || attachment.attachment_id || attachment.name}><FileText />{attachment.name}<small>{attachment.size ? `${Math.ceil(attachment.size / 1024)} КБ` : ""}</small></span>)}</div>}
              {message.drafts.map((draft) => <button className="mail-draft-card" key={draft.id} onClick={() => setComposer({
                draft, mode: draft.mode, replyTo: message, projectId: draft.project_id,
                contractId: draft.contract_id || 0, to: addressText(draft.to), cc: addressText(draft.cc),
                bcc: addressText(draft.bcc), subject: draft.subject,
                body: editorHtml(draft.body, mailSettings.default_font, mailSettings.default_font_size, mailSettings.default_text_color), dirty: false,
              })}>{statusIcon(draft.status)}<span><strong>{draft.subject}</strong><small>{statusLabel(draft.status)} · версия {draft.revision}</small></span><ChevronRight /></button>)}
              <button className="mail-workflow" onClick={() => void setMessageStatus(message)}>{message.status === "in_progress" ? "Отметить обработанным" : "Взять в работу"}</button>
            </article>)}
          </div>
        </> : <div className="mail-empty reading"><Mail /><strong>Выберите письмо</strong><span>Здесь появятся переписка, AI-сводка и действия.</span></div>}
      </section>
    </div>}

    {composer && <div className="mail-composer" role="dialog" aria-modal="true" aria-labelledby="mail-composer-title">
      <header><div><span className="eyebrow">{composer.mode === "compose" ? "НОВОЕ ПИСЬМО" : composer.mode === "reply" ? "ОТВЕТ" : composer.mode === "reply_all" ? "ОТВЕТ ВСЕМ" : "ПЕРЕСЫЛКА"}</span><h2 id="mail-composer-title">{composer.draft ? `Черновик · версия ${composer.draft.revision}` : "Новый черновик"}</h2></div><button aria-label="Закрыть редактор" onClick={() => setComposer(null)}><XCircle /></button></header>
      <div className="mail-composer-fields">
        <label>Проект<select value={composer.projectId} onChange={(event) => updateComposer({ projectId: Number(event.target.value), contractId: 0 })}>{projects.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
        <label>Договор<select value={composer.contractId} onChange={(event) => updateComposer({ contractId: Number(event.target.value) })}><option value={0}>Без договора</option>{contracts.filter((item) => item.project_id === composer.projectId).map((item) => <option value={item.id} key={item.id}>{item.number} — {item.title}</option>)}</select></label>
        <label className="wide">Кому<input value={composer.to} onChange={(event) => updateComposer({ to: event.target.value })} placeholder="name@company.ru" /></label>
        <button className="mail-copy-toggle" onClick={() => setShowCopy((value) => !value)}>{showCopy ? "Скрыть копии" : "Копия / скрытая"}</button>
        {showCopy && <><label className="wide">Копия<input value={composer.cc} onChange={(event) => updateComposer({ cc: event.target.value })} /></label><label className="wide">Скрытая копия<input value={composer.bcc} onChange={(event) => updateComposer({ bcc: event.target.value })} /></label></>}
        <label className="wide">Тема<input ref={subjectRef} value={composer.subject} onChange={(event) => updateComposer({ subject: event.target.value })} /></label>
        <div className="wide mail-editor-field"><strong>Текст письма</strong><RichTextEditor value={composer.body} onChange={(body) => updateComposer({ body })} font={mailSettings.default_font} fontSize={mailSettings.default_font_size} color={mailSettings.default_text_color} disabled={Boolean(busy)} /></div>
      </div>
      <section className="mail-ai-compose">
        <button type="button" className="mail-ai-toggle" onClick={() => setShowAiAssist((value) => !value)}><Sparkles />Помощь Gemini</button>
        {showAiAssist && <div className="mail-ai-panel">
          <div><strong>AI-помощник по тексту письма</strong><small>Gemini только предлагает текст и ничего не отправляет.</small></div>
          <textarea value={aiInstruction} onChange={(event) => setAiInstruction(event.target.value)} placeholder={composer.replyTo ? "Например: подтвердить получение и запросить уточнённый срок" : "Опишите, кому и о чём нужно написать"} />
          <label>Тон<select value={aiTone} onChange={(event) => setAiTone(event.target.value as typeof aiTone)}><option value="business">Деловой</option><option value="neutral">Нейтральный</option><option value="friendly">Дружелюбный</option></select></label>
          <div className="mail-ai-actions">
            <button disabled={Boolean(busy)} onClick={() => void runAiAssist(composer.replyTo ? "reply" : "compose")}>{busy === "ai" ? "Gemini думает…" : composer.replyTo ? "Подготовить ответ" : "Написать письмо"}</button>
            <button disabled={Boolean(busy)} onClick={() => void runAiAssist("improve")}>Улучшить</button>
            <button disabled={Boolean(busy)} onClick={() => void runAiAssist("shorten")}>Сократить</button>
            <button disabled={Boolean(busy)} onClick={() => void runAiAssist("formal")}>Официальнее</button>
            <button disabled={Boolean(busy)} onClick={() => void runAiAssist("friendly")}>Мягче</button>
          </div>
          {aiNotes && <p role="status">{aiNotes}</p>}
        </div>}
      </section>
      {composer.replyTo?.attachments.length ? <p className="mail-attachment-warning"><Paperclip /> Вложения исходного письма показаны в переписке, но не добавляются автоматически.</p> : null}
      {composer.draft && <div className={`mail-delivery-state ${composer.draft.status}`} aria-live="polite">{statusIcon(composer.draft.status)}<div><strong>{statusLabel(composer.draft.status)}</strong>{composer.draft.safe_error && <p>{composer.draft.safe_error}</p>}</div></div>}
      {composer.dirty && composer.draft?.approved_revision && <p className="mail-version-warning"><AlertTriangle /> Текст изменён. Сохраните и подтвердите новую версию перед отправкой.</p>}
      {!capabilities.versioned_approval && <p className="mail-version-warning"><AlertTriangle /> Адаптер не подтвердил поддержку версионированного согласования. Отправка заблокирована.</p>}
      <footer>
        <button className="secondary" disabled={Boolean(busy)} onClick={() => setComposer(null)}>Закрыть</button>
        <button className="secondary" disabled={Boolean(busy) || !composer.dirty && Boolean(composer.draft)} onClick={() => void saveDraft()}>{busy === "save" ? "Сохраняю…" : "Сохранить черновик"}</button>
        <button disabled={Boolean(busy) || !composer.draft || composer.dirty || composer.draft.status !== "draft"} onClick={() => void approveDraft()}>{busy === "approve" ? "Подтверждаю…" : "Подтвердить текущую версию"}</button>
        <button className="send" disabled={Boolean(busy) || !approvedCurrent || !capabilities.can_send || !capabilities.versioned_approval} onClick={() => setConfirmSend(true)}><Send />Отправить</button>
      </footer>
    </div>}

    {confirmSend && composer?.draft && <div className="mail-confirm-backdrop" role="presentation"><section className="mail-send-confirm" role="alertdialog" aria-modal="true" aria-labelledby="mail-confirm-title">
      <Send /><h2 id="mail-confirm-title">Отправить подтверждённую версию {composer.draft.revision}?</h2>
      <dl><div><dt>Кому</dt><dd>{composer.to}</dd></div>{composer.cc && <div><dt>Копия</dt><dd>{composer.cc}</dd></div>}{composer.bcc && <div><dt>Скрытая копия</dt><dd>{composer.bcc}</dd></div>}<div><dt>Тема</dt><dd>{composer.subject}</dd></div><div><dt>Проект</dt><dd>{projects.find((item) => item.id === composer.projectId)?.name}</dd></div></dl>
      <p>После отправки письмо нельзя отозвать средствами PU Workspace.</p>
      <footer><button className="secondary" onClick={() => setConfirmSend(false)}><Undo2 />Вернуться к проверке</button><button className="send" onClick={() => void sendDraft()}><Send />Отправить версию {composer.draft.revision}</button></footer>
    </section></div>}
    {showSettings && <MailSettingsDialog settings={mailSettings} busy={settingsBusy} onClose={() => setShowSettings(false)} onSave={saveMailSettings} />}
  </section>;
}
