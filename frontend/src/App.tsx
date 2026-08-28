import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Bell,
  Bot,
  CalendarDays,
  ChevronLeft,
  ClipboardCheck,
  FileText,
  FolderKanban,
  FolderTree,
  GitPullRequest,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Mail,
  Menu,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  ShieldCheck,
  Users,
  Wallet,
} from "lucide-react";

type Project = { id: number; name: string };
type ProjectStats = {
  attention: number;
  open_tasks: number;
  open_risks: number;
  documents: number;
};
type Summary = {
  attention: number;
  open_tasks: number;
  overdue_tasks: number;
  open_risks: number;
  pending_decisions: number;
  drafts: number;
  documents: number;
  open_obligations: number;
  overdue_obligations: number;
  upcoming_meetings: number;
  unread_notifications: number;
};
type DocumentCard = {
  document_id?: number;
  name: string;
  tasks: number;
  risks: number;
  decisions: number;
  drafts: number;
  attention: number;
};
type DocumentRow = {
  id: number;
  name: string;
  external_id?: string;
  mime_type?: string;
  source: string;
  status: string;
  current_version: number;
  summary?: string;
};
type DocumentDetail = DocumentRow & {
  versions: { version: number; created_at: string }[];
  links: { tasks: number; risks: number; decisions: number; drafts: number };
};
type TaskRow = {
  id: number;
  title: string;
  status: string;
  priority: string;
  due_date?: string;
  assignee_name: string;
  source_file_name: string;
  source_excerpt: string;
  confidence: number;
  needs_review: boolean;
  message_id?: number;
  external_action_status: string;
  google_task_id?: string;
  google_calendar_event_id?: string;
};
type RiskRow = {
  id: number;
  title: string;
  kind: string;
  criticality: string;
  status: string;
  action_note?: string;
  source_name: string;
  confidence: number;
};
type DecisionRow = {
  id: number;
  question: string;
  status: string;
  decision_text?: string;
  reason?: string;
  source_name: string;
  confidence: number;
};
type ResponseDraft = {
  id: number;
  subject: string;
  body: string;
  status: string;
  source_file_name: string;
  source_excerpt: string;
  confidence: number;
  reviewer_name: string;
};
type AuditRow = {
  id: number;
  action: string;
  entity_type: string;
  entity_id?: number;
  details?: string;
  created_at: string;
};
type MemberRow = {
  membership_id: number;
  user_id: number;
  name: string;
  email: string;
  role: string;
};
type GoogleState = {
  authorized: boolean;
  tasks_authorized: boolean;
  calendar_authorized: boolean;
  gmail_authorized: boolean;
};
type AIProjectPolicy = {
  project_id: number;
  mode: "local_only" | "external_allowed" | "redacted" | "metadata_only";
  dlp_enabled: boolean;
  prompt_version: string;
};
type ProcessingQueue = {
  summary: { active: number; failed: number; dead_letter: number };
  snapshots: Array<{
    id: number;
    status: string;
    analysis_status: string;
    retry_count: number;
    analysis_retry_count: number;
    error?: string;
  }>;
  sessions: Array<{
    id: number;
    status: string;
    progress: number;
    retry_count: number;
    error_message?: string;
  }>;
};
type SystemState = {
  ready: boolean;
  google_drive_ready: boolean;
  telegram_ready: boolean;
  checks: Record<string, { ok: boolean; required: boolean; message: string }>;
};
type CurrentUser = {
  id: number;
  name: string;
  email: string;
  is_admin: boolean;
};
type ProposalAction = {
  id: number;
  source: string;
  proposed_name: string;
  target_folder: string;
  edited_name?: string;
  edited_folder?: string;
  user_decision: string;
  confidence: number;
  special_case?: string;
  reasoning: string;
};
type Proposal = {
  id: number;
  folder_name: string;
  status: string;
  copy_folder_id: string;
  originals_modified: boolean;
  note?: string;
  actions: ProposalAction[];
};
type ContractRow = {
  id: number;
  number: string;
  title: string;
  counterparty?: string;
  signed_at?: string;
  status: string;
  source_document_id?: number;
  notes?: string;
};
type AnalysisResult = {
  status: string;
  documents?: number;
  tasks?: number;
  risks?: number;
  drafts?: number;
  error?: string;
};
type Snapshot = {
  id: number;
  status: string;
  item_count: number;
  source_folder: string;
  source_external_id: string;
  is_primary: boolean;
  analyzed: boolean;
  analysis_status?: string;
  analysis_result?: AnalysisResult;
  analysis_error?: string;
  created_at: string;
  completed_at?: string;
};
type DriveFolder = {
  id: string;
  name: string;
  modifiedTime?: string;
  registered: boolean;
  is_primary: boolean;
  analyzed: boolean;
  analysis_status?: string;
  analysis_result?: AnalysisResult;
  analysis_error?: string;
  snapshot_id?: number;
  snapshot_status?: string;
  item_count?: number;
};
type InboxTask = {
  id: number;
  title: string;
  due_date?: string;
  confidence: number;
  external_action_status: string;
  google_task_id?: string;
  google_calendar_event_id?: string;
};
type InboxDraft = {
  id: number;
  subject: string;
  body: string;
  status: string;
  confidence: number;
};
type InboxRisk = {
  id: number;
  title: string;
  criticality: string;
  status: string;
  confidence: number;
  source_excerpt: string;
};
type InboxMessage = {
  id: number;
  project_id: number;
  contract_id?: number;
  source_type: string;
  source_name: string;
  source_sender?: string;
  source_thread_id?: string;
  source_url?: string;
  content: string;
  attachments: { name: string; mime_type: string; size: number }[];
  summary: string;
  context_confidence: number;
  context_evidence: string;
  context_confirmed: boolean;
  status: string;
  created_at: string;
  tasks: InboxTask[];
  drafts: InboxDraft[];
  risks: InboxRisk[];
};
type ObligationRow = {
  id: number;
  contract_id?: number;
  task_id?: number;
  title: string;
  status: string;
  due_date?: string;
  result_note?: string;
  source_type: string;
  source_name: string;
  source_excerpt: string;
  confidence: number;
};
type MeetingRow = {
  id: number;
  contract_id?: number;
  title: string;
  scheduled_at?: string;
  participants?: string;
  agenda?: string;
  minutes?: string;
  status: string;
};
type NotificationRow = {
  id: number;
  kind: string;
  title: string;
  body: string;
  entity_type: string;
  entity_id: number;
  is_read: boolean;
  created_at: string;
};
type FinanceOverview = {
  summary: {
    budget_planned: number;
    budget_actual: number;
    budget_forecast: number;
    budget_variance: number;
    cash_balance_forecast: number;
    cash_gap: number;
    cash_gap_date?: string;
    delayed_schedule: number;
    late_procurement: number;
    acts_pending: number;
  };
  baselines: {
    id: number;
    name: string;
    version: number;
    status: string;
    note?: string;
  }[];
  schedule: {
    id: number;
    baseline_id: number;
    title: string;
    planned_finish?: string;
    planned_progress: number;
    actual_progress: number;
    status: string;
  }[];
  budget: {
    id: number;
    category: string;
    description: string;
    planned_amount: number;
    actual_amount: number;
    forecast_amount: number;
    currency: string;
    status: string;
  }[];
  cash_flow: {
    id: number;
    direction: string;
    title: string;
    planned_date: string;
    planned_amount: number;
    actual_amount: number;
    status: string;
  }[];
  procurement: {
    id: number;
    title: string;
    supplier?: string;
    stage: string;
    planned_delivery?: string;
    planned_amount: number;
    actual_amount: number;
  }[];
  acts: {
    id: number;
    number: string;
    title: string;
    act_date?: string;
    amount: number;
    status: string;
  }[];
};

const items = [
  [LayoutDashboard, "Рабочий центр"],
  [FolderKanban, "Проекты"],
  [FileText, "Договоры"],
  [FileText, "Документы"],
  [GitPullRequest, "Предложения"],
  [ListTodo, "Задачи"],
  [ClipboardCheck, "Обязательства"],
  [AlertTriangle, "Риски и решения"],
  [Users, "Совещания"],
  [Bell, "Уведомления"],
  [Mail, "Письма"],
  [Bot, "AI Secretary"],
  [Wallet, "Исполнение и финансы"],
  [CalendarDays, "Интеграции"],
  [ShieldCheck, "Журнал"],
  [Settings, "Настройки"],
] as const;
const targetFolders = [
  "00_НЕРАЗОБРАННОЕ",
  "01_УПРАВЛЕНИЕ ПРОЕКТОМ",
  "02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ",
  "03_ФИНАНСЫ И СМЕТЫ",
  "04_ПРОЕКТИРОВАНИЕ",
  "05_ЗАКУПКИ И ПОСТАВКИ",
  "06_ПОДРЯДЧИКИ И КОНТРАГЕНТЫ",
  "07_ПЕРЕПИСКА И СОГЛАСОВАНИЯ",
  "08_ИСПОЛНЕНИЕ И ОТЧЁТНОСТЬ",
  "09_ЗАКРЫТИЕ ПРОЕКТА",
  "99_АРХИВ",
];

async function api(path: string, options: RequestInit = {}) {
  const token = sessionStorage.getItem("pu_token");
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  async function submit() {
    try {
      const d = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      sessionStorage.setItem("pu_token", d.access_token);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="login-page">
      <div className="login-card">
        <div className="brand-mark">PU</div>
        <h1>Вход в PU Workspace</h1>
        <p>Единое рабочее пространство проектов и документов</p>
        <label>
          Email
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
          />
        </label>
        <label>
          Пароль
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </label>
        <button onClick={submit}>Войти</button>
        {error && <div className="error">{error}</div>}
        <a href="/">Открыть прежний интерфейс</a>
      </div>
    </div>
  );
}

export function App() {
  const [ready, setReady] = useState(false),
    [collapsed, setCollapsed] = useState(false),
    [mobile, setMobile] = useState(false);
  const [active, setActive] = useState("Рабочий центр"),
    [query, setQuery] = useState(""),
    [newProjectName, setNewProjectName] = useState(""),
    [newContractNumber, setNewContractNumber] = useState(""),
    [newContractTitle, setNewContractTitle] = useState(""),
    [newCounterparty, setNewCounterparty] = useState(""),
    [projectStats, setProjectStats] = useState<Record<number, ProjectStats>>(
      {},
    ),
    [taskFilter, setTaskFilter] = useState("open"),
    [tasks, setTasks] = useState<TaskRow[]>([]),
    [risks, setRisks] = useState<RiskRow[]>([]),
    [decisions, setDecisions] = useState<DecisionRow[]>([]),
    [drafts, setDrafts] = useState<ResponseDraft[]>([]),
    [inbox, setInbox] = useState<InboxMessage[]>([]),
    [expandedInboxId, setExpandedInboxId] = useState<number | null>(null),
    [inboxFilter, setInboxFilter] = useState("all"),
    [incomingName, setIncomingName] = useState(""),
    [incomingText, setIncomingText] = useState(""),
    [contracts, setContracts] = useState<ContractRow[]>([]),
    [documentRows, setDocumentRows] = useState<DocumentRow[]>([]),
    [selectedDocument, setSelectedDocument] = useState<DocumentDetail | null>(
      null,
    );
  const [projects, setProjects] = useState<Project[]>([]),
    [projectId, setProjectId] = useState(0),
    [summary, setSummary] = useState<Summary | null>(null),
    [documents, setDocuments] = useState<DocumentCard[]>([]),
    [snapshots, setSnapshots] = useState<Snapshot[]>([]),
    [folders, setFolders] = useState<DriveFolder[]>([]),
    [proposals, setProposals] = useState<Proposal[]>([]),
    [auditLogs, setAuditLogs] = useState<AuditRow[]>([]),
    [members, setMembers] = useState<MemberRow[]>([]),
    [googleState, setGoogleState] = useState<GoogleState | null>(null),
    [aiPolicy, setAiPolicy] = useState<AIProjectPolicy | null>(null),
    [processingQueue, setProcessingQueue] = useState<ProcessingQueue | null>(null),
    [systemState, setSystemState] = useState<SystemState | null>(null),
    [currentUser, setCurrentUser] = useState<CurrentUser | null>(null),
    [showSources, setShowSources] = useState(false),
    [busyFolder, setBusyFolder] = useState(""),
    [busyProposal, setBusyProposal] = useState(0),
    [busyAll, setBusyAll] = useState(false),
    [gmailSyncing, setGmailSyncing] = useState(false),
    [gmailSyncStatus, setGmailSyncStatus] = useState(""),
    [notice, setNotice] = useState(""),
    [error, setError] = useState("");
  const [obligations, setObligations] = useState<ObligationRow[]>([]),
    [meetings, setMeetings] = useState<MeetingRow[]>([]),
    [notifications, setNotifications] = useState<NotificationRow[]>([]),
    [newMeetingTitle, setNewMeetingTitle] = useState(""),
    [newMeetingDate, setNewMeetingDate] = useState(""),
    [newMeetingAgenda, setNewMeetingAgenda] = useState("");
  const [finance, setFinance] = useState<FinanceOverview | null>(null),
    [financeKind, setFinanceKind] = useState("budget"),
    [financeTitle, setFinanceTitle] = useState(""),
    [financeAmount, setFinanceAmount] = useState(""),
    [financeDate, setFinanceDate] = useState(""),
    [financeExtra, setFinanceExtra] = useState("");
  async function load() {
    try {
      setError("");
      const p = await api("/projects/");
      setProjects(p.projects);
      const id = projectId || p.projects[0]?.id || 0;
      if (id) {
        setProjectId(id);
        const [
          d,
          s,
          t,
          r,
          g,
          docs,
          responseDrafts,
          inboxData,
          proposalData,
          contractData,
          allStats,
          google,
          health,
          team,
          me,
          audit,
          policy,
          queue,
        ] = await Promise.all([
          api(`/dashboard/project?project_id=${id}`),
          api(`/projects/${id}/snapshots`),
          api(`/tasks?project_id=${id}`),
          api(`/governance/risks?project_id=${id}`),
          api(`/governance/decisions?project_id=${id}`),
          api(`/projects/${id}/documents`),
          api(`/response-drafts?project_id=${id}`),
          api(`/ai-secretary/inbox?project_id=${id}`).catch(() => ({
            messages: [],
          })),
          api(`/organizer/proposals?project_id=${id}`).catch(() => ({
            proposals: [],
          })),
          api(`/projects/${id}/contracts`).catch(() => ({ contracts: [] })),
          Promise.all(
            p.projects.map(async (item: Project) => ({
              id: item.id,
              summary: (await api(`/dashboard/project?project_id=${item.id}`))
                .summary,
            })),
          ),
          api(`/projects/${id}/google/status`).catch(() => null),
          api("/api/readiness").catch(() => null),
          api(`/projects/${id}/members`).catch(() => ({ members: [] })),
          api("/auth/me").catch(() => null),
          api("/history/audit?limit=100").catch(() => ({ logs: [] })),
          api(`/projects/${id}/ai-policy`).catch(() => null),
          api(`/projects/${id}/processing-queue`).catch(() => null),
        ]);
        setSummary(d.summary);
        setDocuments(d.documents);
        setSnapshots(s.snapshots);
        setTasks(t.tasks);
        setRisks(r.risks);
        setDecisions(g.decisions);
        setDocumentRows(docs.documents);
        setDrafts(responseDrafts.drafts);
        setInbox(inboxData.messages);
        setProposals(proposalData.proposals);
        setContracts(contractData.contracts);
        setProjectStats(
          Object.fromEntries(allStats.map((item) => [item.id, item.summary])),
        );
        setGoogleState(google);
        setAiPolicy(policy);
        setProcessingQueue(queue);
        setSystemState(health);
        setMembers(team.members);
        setCurrentUser(me);
        setAuditLogs(audit.logs);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function loadIntegrations() {
    if (!projectId) return;
    try {
      setError("");
      const [google, health] = await Promise.all([
        api(`/projects/${projectId}/google/status`),
        api("/api/readiness"),
      ]);
      setGoogleState(google);
      setSystemState(health);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function createProject() {
    const name = newProjectName.trim();
    if (!name) return;
    try {
      setError("");
      const created = await api("/projects/", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setNewProjectName("");
      setProjectId(created.id);
      setNotice(`Проект «${created.name}» создан`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function connectGoogle() {
    try {
      const result = await api(`/projects/${projectId}/google/auth`);
      window.location.href = result.authorization_url;
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function syncGmail(options: { silent?: boolean } = {}) {
    if (gmailSyncing || !projectId) return;
    try {
      if (!options.silent) setError("");
      setGmailSyncing(true);
      if (!options.silent) setGmailSyncStatus("Получаю последние письма за 7 дней…");
      const result = await api(`/projects/${projectId}/gmail/sync`, {
        method: "POST",
        body: JSON.stringify({ query: "newer_than:7d", max_results: 25 }),
      });
      const checkedAt = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      const message = `Проверено ${checkedAt}. Новых: ${result.processed}. Уже загружено: ${result.skipped}. Ошибок: ${result.failed}.`;
      setGmailSyncStatus(message);
      if (!options.silent || result.processed > 0) setNotice(`Gmail: ${message}`);
      if (result.processed > 0 || !options.silent) await load();
    } catch (e) {
      const message = (e as Error).message;
      setGmailSyncStatus(`Не удалось получить письма: ${message}`);
      if (!options.silent) setError(message);
    } finally {
      setGmailSyncing(false);
    }
  }
  function openGmailResults() {
    setActive("AI Secretary");
  }
  async function saveAIPolicy() {
    if (!projectId || !aiPolicy) return;
    try {
      setError("");
      const saved = await api(`/projects/${projectId}/ai-policy`, {
        method: "PATCH",
        body: JSON.stringify({
          mode: aiPolicy.mode,
          dlp_enabled: aiPolicy.dlp_enabled,
        }),
      });
      setAiPolicy(saved);
      setNotice("Политика AI и защиты данных сохранена");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function retrySnapshot(snapshotId: number) {
    try {
      setError("");
      await api(`/projects/${projectId}/snapshots/${snapshotId}/retry-build`, {
        method: "POST",
      });
      setNotice(`Повтор снимка №${snapshotId} поставлен в очередь`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function retryOrganizerSession(sessionId: number) {
    try {
      setError("");
      await api(`/organizer/sessions/${sessionId}/retry`, { method: "POST" });
      setNotice(`Повтор обработки №${sessionId} поставлен в очередь`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function createContract() {
    if (!newContractNumber.trim() || !newContractTitle.trim()) return;
    try {
      await api(`/projects/${projectId}/contracts`, {
        method: "POST",
        body: JSON.stringify({
          number: newContractNumber.trim(),
          title: newContractTitle.trim(),
          counterparty: newCounterparty.trim() || undefined,
        }),
      });
      setNewContractNumber("");
      setNewContractTitle("");
      setNewCounterparty("");
      setNotice("Договор добавлен в проектный контекст");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function openSources() {
    try {
      setError("");
      const d = await api(`/projects/${projectId}/source-folders/discover`);
      setFolders(d.folders);
      setShowSources(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function queueFolder(folder: DriveFolder) {
    try {
      const queued = await api(
        `/projects/${projectId}/source-folders/${folder.id}/snapshot-queue`,
        { method: "POST" },
      );
      setFolders((items) =>
        items.map((item) =>
          item.id === folder.id
            ? {
                ...item,
                registered: true,
                snapshot_id: queued.id,
                snapshot_status: queued.status,
                item_count: 0,
              }
            : item,
        ),
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function makePrimary(folder: DriveFolder) {
    try {
      setBusyFolder(folder.id);
      setError("");
      await api(`/projects/${projectId}/source-folders/${folder.id}/primary`, {
        method: "POST",
      });
      setFolders((items) =>
        items.map((item) => ({ ...item, is_primary: item.id === folder.id })),
      );
      setSnapshots((items) =>
        items.map((item) => ({
          ...item,
          is_primary: item.source_external_id === folder.id,
        })),
      );
      setNotice(`${folder.name} назначена основной рабочей папкой`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyFolder("");
    }
  }
  async function analyzeFolder(folder: DriveFolder) {
    if (!folder.snapshot_id) return;
    try {
      setBusyFolder(folder.id);
      setError("");
      setNotice("");
      const result = await api(
        `/projects/${projectId}/snapshots/${folder.snapshot_id}/analyze`,
        { method: "POST" },
      );
      if (result.already_analyzed) {
        setFolders((items) =>
          items.map((item) =>
            item.id === folder.id ? { ...item, analyzed: true } : item,
          ),
        );
        setNotice(`${folder.name} уже проанализирована`);
      } else {
        setFolders((items) =>
          items.map((item) =>
            item.id === folder.id
              ? { ...item, analysis_status: "analyzing" }
              : item,
          ),
        );
        setNotice(
          `Анализ «${folder.name}» запущен в фоне. Страницу можно закрыть.`,
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyFolder("");
    }
  }
  async function queueAllFolders() {
    try {
      setBusyAll(true);
      setError("");
      const result = await api(
        `/projects/${projectId}/source-folders/snapshot-queue-all`,
        { method: "POST" },
      );
      setNotice(
        result.queued
          ? `В очередь добавлено папок: ${result.queued}. Они будут обработаны последовательно.`
          : "Все доступные папки уже подключены или находятся в очереди.",
      );
      await openSources();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyAll(false);
    }
  }
  async function updateTask(task: TaskRow, status: string) {
    try {
      setError("");
      let result_note: string | undefined;
      if (status === "completed") {
        result_note =
          window.prompt("Укажите подтверждаемый результат выполнения") ||
          undefined;
        if (!result_note) return;
      }
      await api(`/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, result_note }),
      });
      setNotice(
        status === "completed"
          ? "Задача завершена и синхронизирована"
          : "Задача взята в работу",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function updateRisk(risk: RiskRow, status: string) {
    try {
      const action_note =
        status === "confirmed"
          ? undefined
          : window.prompt("Укажите действие или результат по риску") ||
            undefined;
      if (status !== "confirmed" && !action_note) return;
      await api(`/governance/risks/${risk.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, action_note }),
      });
      setNotice("Статус риска обновлён");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function updateDecision(item: DecisionRow, status: string) {
    try {
      const note =
        window.prompt(
          status === "dismissed"
            ? "Укажите основание отклонения"
            : "Зафиксируйте принятое решение",
        ) || undefined;
      if (!note) return;
      await api(`/governance/decisions/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status,
          decision_text: status === "dismissed" ? undefined : note,
          reason: status === "dismissed" ? note : undefined,
        }),
      });
      setNotice("Решение зафиксировано");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function updateDraft(item: ResponseDraft, status: string) {
    try {
      await api(`/response-drafts/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status,
          subject: item.subject,
          body: item.body,
        }),
      });
      setNotice(
        status === "approved"
          ? "Черновик подтверждён. Отправка остаётся под вашим контролем."
          : "Черновик отклонён",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function ingestMessage() {
    if (!incomingName.trim() || !incomingText.trim()) return;
    try {
      await api("/ai-secretary/inbox", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          source_type: "manual",
          source_name: incomingName.trim(),
          content: incomingText.trim(),
        }),
      });
      setIncomingName("");
      setIncomingText("");
      setNotice(
        "Сообщение обработано. Внешние задачи и события пока только предложены.",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function confirmMessageContext(message: InboxMessage) {
    try {
      await api(`/ai-secretary/inbox/${message.id}/confirm-context`, {
        method: "POST",
        body: JSON.stringify({ contract_id: message.contract_id || null }),
      });
      setNotice("Связь сообщения с проектом и договором подтверждена");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function updateInboxStatus(message: InboxMessage, status: string) {
    try {
      const updated = await api(`/ai-secretary/inbox/${message.id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setInbox((rows) => rows.map((row) => row.id === message.id ? updated : row));
      setNotice(status === "completed" ? "Письмо отмечено обработанным" : "Письмо взято в работу");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function approveExternal(task: InboxTask) {
    if (
      !window.confirm(
        `Создать подтверждённую задачу «${task.title}» в Google Tasks${task.due_date ? " и Calendar" : ""}?`,
      )
    )
      return;
    try {
      const result = await api(`/tasks/${task.id}/approve-external`, {
        method: "POST",
        body: JSON.stringify({
          create_google_task: true,
          create_calendar_event: Boolean(task.due_date),
        }),
      });
      setNotice(
        result.external_action_status === "executed"
          ? "Задача создана во внешних сервисах"
          : "Не удалось создать внешнее действие; подробности сохранены",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function reviewInboxDraft(
    messageId: number,
    draft: InboxDraft,
    status: string,
  ) {
    try {
      await api(`/response-drafts/${draft.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status,
          subject: draft.subject,
          body: draft.body,
        }),
      });
      setNotice(
        status === "approved"
          ? "Черновик подтверждён, но не отправлен"
          : "Черновик отклонён",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function sendGmailDraft(draft: InboxDraft) {
    if (!window.confirm("Отправить подтверждённый ответ через Gmail?")) return;
    try {
      const result = await api(`/response-drafts/${draft.id}/send-gmail`, {
        method: "POST",
      });
      setNotice(
        result.already_sent
          ? "Это письмо уже было отправлено ранее"
          : "Ответ отправлен через Gmail и записан в аудит",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function editProposalAction(item: ProposalAction, decision: string) {
    try {
      await api(`/organizer/actions/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          decision,
          edited_name: item.edited_name || item.proposed_name,
          edited_folder: item.edited_folder || item.target_folder,
        }),
      });
      setProposals((rows) =>
        rows.map((proposal) => ({
          ...proposal,
          actions: proposal.actions.map((action) =>
            action.id === item.id
              ? { ...action, user_decision: decision }
              : action,
          ),
        })),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function saveProposalAction(proposalId: number, actionId: number) {
    const action = proposals
      .find((item) => item.id === proposalId)
      ?.actions.find((item) => item.id === actionId);
    if (action) await editProposalAction(action, "edited");
  }
  async function approveSafe(proposal: Proposal) {
    try {
      setBusyProposal(proposal.id);
      const result = await api(
        `/organizer/proposals/${proposal.id}/approve-safe`,
        { method: "POST" },
      );
      setNotice(
        `Безопасных действий подтверждено: ${result.approved}. Особые случаи пропущены.`,
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyProposal(0);
    }
  }
  async function applyProposal(proposal: Proposal) {
    try {
      setBusyProposal(proposal.id);
      const result = await api(`/organizer/proposals/${proposal.id}/apply`, {
        method: "POST",
      });
      setNotice(
        `Применено к безопасной копии: переименовано ${result.stats.renamed}, перемещено ${result.stats.moved}.`,
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyProposal(0);
    }
  }
  async function rollbackProposal(proposal: Proposal) {
    if (!window.confirm("Откатить применённые изменения в безопасной копии?"))
      return;
    try {
      setBusyProposal(proposal.id);
      const result = await api(`/organizer/proposals/${proposal.id}/rollback`, {
        method: "POST",
      });
      setNotice(
        `Откат завершён: ${result.rolled_back}, ошибок: ${result.errors}.`,
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyProposal(0);
    }
  }
  async function applyOneToSource(proposal: Proposal, action: ProposalAction) {
    const phrase = window.prompt(
      `Будет переименован ОДИН оригинальный файл:\n${action.source}\nВведите APPLY_ONE_TO_SOURCE`,
    );
    if (phrase !== "APPLY_ONE_TO_SOURCE") return;
    try {
      setBusyProposal(proposal.id);
      const result = await api(
        `/organizer/proposals/${proposal.id}/apply-source-one`,
        {
          method: "POST",
          body: JSON.stringify({ action_id: action.id, confirmation: phrase }),
        },
      );
      setNotice(
        `Оригинал безопасно переименован. Выполнено: ${result.stats.renamed}. Доступен rollback.`,
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyProposal(0);
    }
  }
  async function openDocument(item: DocumentRow) {
    try {
      setSelectedDocument(
        await api(`/projects/${projectId}/documents/${item.id}`),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    api("/auth/me")
      .then(() => setReady(true))
      .catch(() => setReady(false));
  }, []);
  useEffect(() => {
    if (ready) load();
  }, [ready, projectId]);
  useEffect(() => {
    if (ready && projectId && active === "Интеграции") loadIntegrations();
  }, [ready, projectId, active]);
  useEffect(() => {
    if (!ready || !projectId || !googleState?.gmail_authorized) return;
    const initial = window.setTimeout(() => syncGmail({ silent: true }), 12000);
    const timer = window.setInterval(() => syncGmail({ silent: true }), 5 * 60 * 1000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [ready, projectId, googleState?.gmail_authorized]);
  useEffect(() => {
    if (!ready || !snapshots.some((item) => item.status === "building")) return;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [ready, projectId, snapshots.some((item) => item.status === "building")]);
  useEffect(() => {
    if (
      !ready ||
      !showSources ||
      !folders.some(
        (item) =>
          item.snapshot_status === "building" ||
          item.analysis_status === "analyzing",
      )
    )
      return;
    const timer = window.setInterval(() => {
      openSources();
      load();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [
    ready,
    projectId,
    showSources,
    folders.some(
      (item) =>
        item.snapshot_status === "building" ||
        item.analysis_status === "analyzing",
    ),
  ]);
  useEffect(() => {
    if (active === "Документы" && documentRows.length && !selectedDocument)
      openDocument(documentRows[0]);
  }, [active, documentRows.length, projectId]);
  async function loadManagement() {
    if (!projectId) return;
    try {
      const [o, m, n] = await Promise.all([
        api(`/management/obligations?project_id=${projectId}`),
        api(`/management/meetings?project_id=${projectId}`),
        api(`/management/notifications?project_id=${projectId}`),
      ]);
      setObligations(o.obligations);
      setMeetings(m.meetings);
      setNotifications(n.notifications);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    if (ready && projectId) loadManagement();
  }, [ready, projectId]);
  async function updateObligation(item: ObligationRow, status: string) {
    let result_note: string | undefined;
    if (["fulfilled", "breached"].includes(status)) {
      result_note =
        window.prompt(
          status === "fulfilled"
            ? "Укажите результат исполнения"
            : "Укажите основание нарушения",
        ) || undefined;
      if (!result_note) return;
    }
    try {
      await api(`/management/obligations/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, result_note }),
      });
      setNotice("Статус обязательства обновлён");
      await Promise.all([load(), loadManagement()]);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function createMeeting() {
    if (!newMeetingTitle.trim()) return;
    try {
      await api("/management/meetings", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          title: newMeetingTitle.trim(),
          scheduled_at: newMeetingDate
            ? new Date(newMeetingDate).toISOString()
            : null,
          agenda: newMeetingAgenda.trim() || null,
        }),
      });
      setNewMeetingTitle("");
      setNewMeetingDate("");
      setNewMeetingAgenda("");
      setNotice("Совещание добавлено");
      await loadManagement();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function recordMinutes(item: MeetingRow) {
    const minutes =
      window.prompt("Вставьте протокол: решения, поручения, сроки и риски") ||
      "";
    if (!minutes.trim()) return;
    try {
      const result = await api(`/management/meetings/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ minutes, status: "completed" }),
      });
      setNotice(
        `Протокол обработан: задач ${result.tasks}, рисков ${result.risks}, решений ${result.decisions}`,
      );
      await Promise.all([load(), loadManagement()]);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function refreshNotifications() {
    try {
      const result = await api(
        `/management/notifications/refresh?project_id=${projectId}`,
        { method: "POST" },
      );
      setNotifications(result.notifications);
      setNotice(`Непрочитанных уведомлений: ${result.unread}`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function markNotification(item: NotificationRow) {
    try {
      await api(`/management/notifications/${item.id}/read`, {
        method: "POST",
      });
      setNotifications((rows) =>
        rows.map((row) =>
          row.id === item.id ? { ...row, is_read: true } : row,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function loadFinance() {
    if (!projectId) return;
    try {
      setFinance(await api(`/execution/overview?project_id=${projectId}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    if (ready && projectId) loadFinance();
  }, [ready, projectId]);
  async function addFinanceItem() {
    const amount = Number(financeAmount || 0);
    if (!financeTitle.trim()) return;
    try {
      let path = "/execution/budget";
      let body: Record<string, unknown> = { project_id: projectId };
      if (financeKind === "budget")
        body = {
          ...body,
          category: financeExtra.trim() || "Прочее",
          description: financeTitle.trim(),
          planned_amount: amount,
        };
      if (financeKind === "cash-in" || financeKind === "cash-out") {
        path = "/execution/cash-flow";
        body = {
          ...body,
          direction: financeKind === "cash-in" ? "inflow" : "outflow",
          title: financeTitle.trim(),
          planned_date: financeDate,
          planned_amount: amount,
          counterparty: financeExtra.trim() || null,
        };
      }
      if (financeKind === "procurement") {
        path = "/execution/procurement";
        body = {
          ...body,
          title: financeTitle.trim(),
          supplier: financeExtra.trim() || null,
          planned_delivery: financeDate || null,
          planned_amount: amount,
        };
      }
      if (financeKind === "act") {
        path = "/execution/acts";
        body = {
          ...body,
          number: financeExtra.trim() || "б/н",
          title: financeTitle.trim(),
          act_date: financeDate || null,
          amount,
        };
      }
      if (financeKind === "baseline") {
        path = "/execution/baselines";
        body = {
          ...body,
          name: financeTitle.trim(),
          note: financeExtra.trim() || null,
        };
      }
      if (financeKind === "schedule") {
        const baseline = finance?.baselines.find((x) => x.status === "draft");
        if (!baseline) throw new Error("Сначала создайте черновик версии ГПР");
        path = "/execution/schedule-items";
        body = {
          baseline_id: baseline.id,
          title: financeTitle.trim(),
          planned_finish: financeDate || null,
          planned_progress: amount,
        };
      }
      await api(path, { method: "POST", body: JSON.stringify(body) });
      setFinanceTitle("");
      setFinanceAmount("");
      setFinanceDate("");
      setFinanceExtra("");
      setNotice("Запись создана как предложение и ожидает подтверждения");
      await loadFinance();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function confirmFinance(kind: string, id: number, status: string) {
    try {
      await api(`/execution/${kind}/${id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setNotice("Статус финансовой записи подтверждён и сохранён в аудите");
      await loadFinance();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function updateScheduleActual(id: number) {
    const value = window.prompt("Фактическая готовность, %", "100");
    if (value === null) return;
    const progress = Number(value);
    if (!Number.isFinite(progress) || progress < 0 || progress > 100) {
      setError("Введите число от 0 до 100");
      return;
    }
    try {
      await api(`/execution/schedule-items/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          actual_progress: progress,
          actual_finish:
            progress === 100 ? new Date().toISOString().slice(0, 10) : null,
        }),
      });
      setNotice("Факт по работе ГПР обновлён");
      await loadFinance();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function recordFinanceActual(kind: string, id: number, status: string) {
    const raw = window.prompt("Фактическая сумма, ₽", "0");
    if (raw === null) return;
    const amount = Number(raw);
    if (!Number.isFinite(amount) || amount < 0) {
      setError("Введите корректную сумму");
      return;
    }
    try {
      await api(`/execution/${kind}/${id}/status`, {
        method: "PATCH",
        body: JSON.stringify({
          status,
          actual_amount: amount,
          actual_date: new Date().toISOString().slice(0, 10),
        }),
      });
      setNotice("Фактическое исполнение записано и сохранено в аудите");
      await loadFinance();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!ready) return <Login onDone={() => setReady(true)} />;
  const metrics = [
    ["Требуют внимания", summary?.attention || 0, "warn"],
    ["Обязательства", summary?.open_obligations || 0, ""],
    [
      "Просрочено",
      (summary?.overdue_tasks || 0) + (summary?.overdue_obligations || 0),
      "danger",
    ],
    ["Риски", summary?.open_risks || 0, "warn"],
    ["Ждут решения", summary?.pending_decisions || 0, ""],
    ["Уведомления", summary?.unread_notifications || 0, ""],
  ];
  const latestSnapshot =
    snapshots.find((item) => item.is_primary) || snapshots[0];
  const today = new Date().toISOString().slice(0, 10);
  const visibleTasks = tasks.filter((task) =>
    taskFilter === "all"
      ? true
      : taskFilter === "overdue"
        ? Boolean(
            task.due_date &&
              task.due_date < today &&
              task.status !== "completed",
          )
        : taskFilter === "review"
          ? task.needs_review
          : task.status === "assigned" || task.status === "in_progress",
  );
  const money = (value: number | undefined) =>
    new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency: "RUB",
      maximumFractionDigits: 0,
    }).format(Number(value || 0));
  const visibleDocuments = documentRows.filter(
    (item) =>
      !query ||
      item.name
        .toLocaleLowerCase("ru-RU")
        .includes(query.toLocaleLowerCase("ru-RU")),
  );
  const inboxCounts = {
    all: inbox.length,
    attention: inbox.filter((item) => !item.context_confirmed || item.status !== "completed").length,
    tasks: inbox.filter((item) => item.tasks.length > 0).length,
    drafts: inbox.filter((item) => item.drafts.some((draft) => draft.status !== "sent")).length,
  };
  const visibleInbox = inbox.filter((item) => {
    const matchesQuery = !query || `${item.source_name} ${item.source_sender || ""} ${item.summary}`
      .toLocaleLowerCase("ru-RU").includes(query.toLocaleLowerCase("ru-RU"));
    if (!matchesQuery) return false;
    if (inboxFilter === "attention") return !item.context_confirmed || item.status !== "completed";
    if (inboxFilter === "tasks") return item.tasks.length > 0;
    if (inboxFilter === "drafts") return item.drafts.some((draft) => draft.status !== "sent");
    return true;
  });
  return (
    <div className="shell">
      <aside
        className={`${collapsed ? "collapsed" : ""} ${mobile ? "mobile-open" : ""}`}
      >
        <div className="sidebar-head">
          <div className="brand-mark">PU</div>
          {!collapsed && <strong>PU Workspace</strong>}
          <button className="icon" onClick={() => setCollapsed(!collapsed)}>
            <ChevronLeft />
          </button>
        </div>
        <nav>
          {items.map(([Icon, label]) => (
            <button
              onClick={() => {
                setActive(label);
                setMobile(false);
              }}
              className={label === active ? "active" : ""}
              key={label}
              title={label}
            >
              <Icon />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="profile">
          <div className="avatar">D</div>
          {!collapsed && (
            <div>
              <strong>Администратор</strong>
              <small>Владелец</small>
            </div>
          )}
          <button
            className="icon"
            onClick={() => {
              sessionStorage.removeItem("pu_token");
              setReady(false);
            }}
          >
            <LogOut />
          </button>
        </div>
      </aside>
      <main>
        <header>
          <button
            className="mobile-menu icon"
            onClick={() => setMobile(!mobile)}
          >
            <Menu />
          </button>
          <div>
            <h1>{active}</h1>
            <p>
              {active === "Рабочий центр"
                ? "Главное по проекту на сегодня"
                : "Данные выбранного проекта"}
            </p>
          </div>
          <div className="header-actions">
            <div className="search">
              <Search />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Поиск по проекту"
              />
            </div>
            <select
              value={projectId}
              onChange={(e) => setProjectId(Number(e.target.value))}
            >
              {projects.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button className="icon" onClick={load}>
              <RefreshCw />
            </button>
          </div>
        </header>
        <section className="content">
          {error && <div className="error">{error}</div>}
          {notice && <div className="notice">{notice}</div>}
          {active === "Задачи" ? (
            <section className="card task-register">
              <div className="card-head">
                <div>
                  <h2>Реестр задач</h2>
                  <p>
                    Автоматически выделенные поручения с проверяемым источником
                  </p>
                </div>
                <div className="task-filters">
                  {[
                    ["open", "Открытые"],
                    ["overdue", "Просроченные"],
                    ["review", "На проверку"],
                    ["all", "Все"],
                  ].map(([id, label]) => (
                    <button
                      className={taskFilter === id ? "selected" : ""}
                      onClick={() => setTaskFilter(id)}
                      key={id}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="task-list">
                {visibleTasks.map((task) => (
                  <article key={task.id}>
                    <div className={`task-priority ${task.priority}`}></div>
                    <div className="task-body">
                      <strong>{task.title}</strong>
                      <p>
                        {task.source_file_name} · {task.assignee_name} ·
                        уверенность {Math.round(task.confidence * 100)}%
                      </p>
                      <small>{task.source_excerpt}</small>
                    </div>
                    <div className="task-meta">
                      <span
                        className={
                          task.due_date &&
                          task.due_date < today &&
                          task.status !== "completed"
                            ? "overdue"
                            : ""
                        }
                      >
                        {task.due_date || "Без срока"}
                      </span>
                      <span>
                        {task.google_task_id
                          ? "Google Tasks ✓"
                          : task.external_action_status === "proposed"
                            ? "Предложение"
                            : "Локальная"}
                        {task.google_calendar_event_id ? " · Calendar ✓" : ""}
                      </span>
                    </div>
                    <div className="task-actions">
                      {task.external_action_status !== "executed" && (
                        <button onClick={() => approveExternal(task)}>
                          Создать в Google
                        </button>
                      )}
                      {task.status === "assigned" && (
                        <button onClick={() => updateTask(task, "in_progress")}>
                          В работу
                        </button>
                      )}
                      {task.status !== "completed" && (
                        <button
                          className="complete"
                          onClick={() => updateTask(task, "completed")}
                        >
                          Завершить
                        </button>
                      )}
                    </div>
                  </article>
                ))}
                {!visibleTasks.length && (
                  <div className="empty">
                    <ListTodo />
                    <p>Задач в этом фильтре нет</p>
                  </div>
                )}
              </div>
            </section>
          ) : active === "Риски и решения" ? (
            <section className="governance-grid">
              <div className="card">
                <div className="card-head">
                  <div>
                    <h2>Риски</h2>
                    <p>Обнаружено: {risks.length}</p>
                  </div>
                </div>
                <div className="governance-list">
                  {risks.map((risk) => (
                    <article key={risk.id}>
                      <div>
                        <strong>{risk.title}</strong>
                        <p>
                          {risk.source_name} · уверенность{" "}
                          {Math.round(risk.confidence * 100)}%
                        </p>
                        <span
                          className={`governance-status ${risk.criticality}`}
                        >
                          {risk.status}
                        </span>
                      </div>
                      <div className="task-actions">
                        {risk.status === "needs_confirmation" && (
                          <button onClick={() => updateRisk(risk, "confirmed")}>
                            Подтвердить
                          </button>
                        )}
                        {!["resolved", "dismissed"].includes(risk.status) && (
                          <button
                            className="complete"
                            onClick={() => updateRisk(risk, "resolved")}
                          >
                            Закрыть
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              </div>
              <div className="card">
                <div className="card-head">
                  <div>
                    <h2>Решения</h2>
                    <p>
                      Ожидают фиксации:{" "}
                      {
                        decisions.filter(
                          (x) => !["executed", "dismissed"].includes(x.status),
                        ).length
                      }
                    </p>
                  </div>
                </div>
                <div className="governance-list">
                  {decisions.map((item) => (
                    <article key={item.id}>
                      <div>
                        <strong>{item.question}</strong>
                        <p>
                          {item.source_name} · уверенность{" "}
                          {Math.round(item.confidence * 100)}%
                        </p>
                        <span className="governance-status">{item.status}</span>
                      </div>
                      <div className="task-actions">
                        {!["decided", "executed", "dismissed"].includes(
                          item.status,
                        ) && (
                          <button
                            className="complete"
                            onClick={() => updateDecision(item, "decided")}
                          >
                            Принять
                          </button>
                        )}
                        {!["executed", "dismissed"].includes(item.status) && (
                          <button
                            onClick={() => updateDecision(item, "dismissed")}
                          >
                            Отклонить
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            </section>
          ) : (
            <>
              <div className="metrics">
                {metrics.map(([label, value, tone]) => (
                  <article className={String(tone)} key={String(label)}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </article>
                ))}
              </div>
              <div className="grid">
                <section className="card span-2">
                  <div className="card-head">
                    <div>
                      <h2>Что требует внимания</h2>
                      <p>
                        Задачи, риски и решения из подтверждённых источников
                      </p>
                    </div>
                    <button>Открыть реестр</button>
                  </div>
                  {summary?.attention ? (
                    <div className="attention">
                      <AlertTriangle />
                      <div>
                        <strong>
                          {summary.attention} пунктов требуют проверки
                        </strong>
                        <p>
                          Просрочено задач: {summary.overdue_tasks}; открытых
                          рисков: {summary.open_risks}; решений:{" "}
                          {summary.pending_decisions}.
                        </p>
                      </div>
                    </div>
                  ) : (
                    <div className="empty">
                      <ShieldCheck />
                      <p>Критичных пунктов нет</p>
                    </div>
                  )}
                </section>
                <section className="card">
                  <div className="card-head">
                    <div>
                      <h2>Быстрые действия</h2>
                      <p>Без изменения оригиналов</p>
                    </div>
                  </div>
                  <div className="quick">
                    <button onClick={() => setActive("Интеграции")}>Подключения и источники</button>
                    <button onClick={() => setActive("Письма")}>Открыть письма</button>
                    <button onClick={() => setActive("Задачи")}>Открыть задачи</button>
                  </div>
                </section>
                <section className="card span-3 dashboard-inbox">
                  <div className="card-head">
                    <div>
                      <h2>Входящие, требующие реакции</h2>
                      <p>Новые письма, неподтверждённый контекст и работа в процессе</p>
                    </div>
                    <button onClick={() => setActive("Письма")}>Все письма</button>
                  </div>
                  <div className="dashboard-inbox-list">
                    {inbox.filter((item) => item.status !== "completed" || !item.context_confirmed).slice(0, 4).map((message) => (
                      <button key={message.id} onClick={() => { setExpandedInboxId(message.id); setActive("Письма"); }}>
                        <Mail />
                        <span>
                          <strong>{message.source_name}</strong>
                          <small>{message.source_sender || message.source_type} · {new Date(message.created_at).toLocaleDateString("ru-RU")}</small>
                        </span>
                        <b>{message.tasks.length ? `${message.tasks.length} задач` : message.drafts.length ? `${message.drafts.length} черновиков` : "Открыть"}</b>
                      </button>
                    ))}
                    {!inbox.some((item) => item.status !== "completed" || !item.context_confirmed) && (
                      <div className="empty"><ShieldCheck /><p>Необработанных входящих нет</p></div>
                    )}
                  </div>
                </section>
                {latestSnapshot && (
                  <section className="card span-3 source-card">
                    <div className="source-icon">
                      <FolderTree />
                    </div>
                    <div>
                      <span className="eyebrow">РАБОЧИЙ ИСТОЧНИК</span>
                      <h2>{latestSnapshot.source_folder}</h2>
                      <p>
                        Виртуальный снимок №{latestSnapshot.id} ·{" "}
                        {latestSnapshot.item_count.toLocaleString("ru-RU")}{" "}
                        объектов · оригиналы не изменяются
                      </p>
                    </div>
                    <span className={`source-status ${latestSnapshot.status}`}>
                      {latestSnapshot.status === "ready"
                        ? "Снимок готов"
                        : latestSnapshot.status}
                    </span>
                    <div className="source-actions">
                      <button onClick={openSources}>Все источники</button>
                      <a
                        className="source-link"
                        href={`https://drive.google.com/drive/folders/${latestSnapshot.source_external_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Открыть в Google Drive
                      </a>
                    </div>
                  </section>
                )}
                {showSources && (
                  <section className="card span-3">
                    <div className="card-head">
                      <div>
                        <h2>Папки для последовательного разбора</h2>
                        <p>
                          Каждая папка получает отдельный виртуальный снимок;
                          файлы не перемещаются.
                        </p>
                      </div>
                      <div className="source-head-actions">
                        <button
                          className="queue-all"
                          disabled={busyAll}
                          onClick={queueAllFolders}
                        >
                          {busyAll ? "Добавление…" : "Подготовить все папки"}
                        </button>
                        <button onClick={() => setShowSources(false)}>
                          Закрыть
                        </button>
                      </div>
                    </div>
                    <div className="source-list">
                      {folders.map((folder) => (
                        <article key={folder.id}>
                          <FolderKanban />
                          <div>
                            <strong>
                              {folder.name}
                              {folder.is_primary && (
                                <span className="primary-label">Основная</span>
                              )}
                            </strong>
                            <p>
                              {folder.snapshot_status === "building"
                                ? "Создаётся виртуальный снимок…"
                                : folder.analysis_status === "analyzing"
                                  ? `Анализируется в фоне · ${(folder.item_count || 0).toLocaleString("ru-RU")} объектов`
                                  : folder.analysis_status === "failed"
                                    ? `Ошибка анализа: ${folder.analysis_error || "можно повторить"}`
                                    : folder.analysis_status === "ready"
                                      ? `Готово: документов ${folder.analysis_result?.documents || 0}, задач ${folder.analysis_result?.tasks || 0}, рисков ${folder.analysis_result?.risks || 0}, черновиков ${folder.analysis_result?.drafts || 0}`
                                      : folder.snapshot_status === "ready"
                                        ? `Снимок №${folder.snapshot_id} готов · ${(folder.item_count || 0).toLocaleString("ru-RU")} объектов${folder.analyzed ? " · проанализирован" : ""}`
                                        : folder.snapshot_status === "failed"
                                          ? "Ошибка снимка — можно повторить"
                                          : folder.modifiedTime
                                            ? `Изменена ${new Date(folder.modifiedTime).toLocaleDateString("ru-RU")}`
                                            : "Google Drive"}
                            </p>
                          </div>
                          <div className="source-row-actions">
                            {folder.registered && !folder.is_primary && (
                              <button
                                disabled={busyFolder === folder.id}
                                onClick={() => makePrimary(folder)}
                              >
                                Сделать основной
                              </button>
                            )}
                            {folder.snapshot_status === "ready" ? (
                              <button
                                className="analyze"
                                disabled={
                                  busyFolder === folder.id ||
                                  folder.analyzed ||
                                  folder.analysis_status === "analyzing"
                                }
                                onClick={() => analyzeFolder(folder)}
                              >
                                {busyFolder === folder.id ||
                                folder.analysis_status === "analyzing"
                                  ? "Анализ…"
                                  : folder.analyzed
                                    ? "Проанализирована"
                                    : folder.analysis_status === "failed"
                                      ? "Повторить анализ"
                                      : "Проанализировать"}
                              </button>
                            ) : (
                              <button
                                disabled={
                                  folder.snapshot_status === "building" ||
                                  busyFolder === folder.id
                                }
                                onClick={() => queueFolder(folder)}
                              >
                                {folder.snapshot_status === "building"
                                  ? "В очереди"
                                  : folder.snapshot_status === "failed"
                                    ? "Повторить"
                                    : "Поставить в очередь"}
                              </button>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  </section>
                )}
                <section className="card span-3">
                  <div className="card-head">
                    <div>
                      <h2>Последние документы</h2>
                      <p>Связанные задачи, риски и решения</p>
                    </div>
                    <button>Все документы</button>
                  </div>
                  <div className="doc-list">
                    {documents.slice(0, 8).map((d) => (
                      <article key={d.name}>
                        <div className="file-icon">
                          <FileText />
                        </div>
                        <div>
                          <strong>{d.name}</strong>
                          <p>
                            Задач {d.tasks} · рисков {d.risks} · решений{" "}
                            {d.decisions}
                          </p>
                        </div>
                        {d.attention > 0 && (
                          <span className="pill">Внимание: {d.attention}</span>
                        )}
                      </article>
                    ))}
                  </div>
                </section>
              </div>
            </>
          )}
        </section>
      </main>
      {active === "Исполнение и финансы" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page finance-page">
            <section className="finance-metrics">
              {[
                ["Бюджет", money(finance?.summary.budget_planned)],
                ["Факт", money(finance?.summary.budget_actual)],
                ["Прогноз", money(finance?.summary.budget_forecast)],
                ["Отклонение", money(finance?.summary.budget_variance)],
                [
                  "Прогноз остатка",
                  money(finance?.summary.cash_balance_forecast),
                ],
                ["Кассовый разрыв", money(finance?.summary.cash_gap)],
              ].map(([label, value]) => (
                <article className="card" key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </article>
              ))}
            </section>
            <section className="card finance-entry">
              <div>
                <h2>Добавить управленческую запись</h2>
                <p>
                  Новая запись создаётся как предложение и не влияет на
                  подтверждённый прогноз.
                </p>
              </div>
              <div>
                <select
                  value={financeKind}
                  onChange={(e) => setFinanceKind(e.target.value)}
                >
                  <option value="budget">Строка бюджета</option>
                  <option value="cash-in">Поступление ДДС</option>
                  <option value="cash-out">Выплата ДДС</option>
                  <option value="procurement">Закупка / поставка</option>
                  <option value="act">Акт</option>
                  <option value="baseline">Версия ГПР</option>
                </select>
                <input
                  value={financeTitle}
                  onChange={(e) => setFinanceTitle(e.target.value)}
                  placeholder="Название"
                />
                {financeKind !== "baseline" && (
                  <input
                    type="number"
                    min="0"
                    value={financeAmount}
                    onChange={(e) => setFinanceAmount(e.target.value)}
                    placeholder="Сумма, ₽"
                  />
                )}
                {["cash-in", "cash-out", "procurement", "act"].includes(
                  financeKind,
                ) && (
                  <input
                    type="date"
                    value={financeDate}
                    onChange={(e) => setFinanceDate(e.target.value)}
                  />
                )}
                <input
                  value={financeExtra}
                  onChange={(e) => setFinanceExtra(e.target.value)}
                  placeholder={
                    financeKind === "budget"
                      ? "Категория"
                      : financeKind === "act"
                        ? "Номер акта"
                        : financeKind === "baseline"
                          ? "Комментарий"
                          : "Контрагент / поставщик"
                  }
                />
                <button
                  disabled={!financeTitle.trim()}
                  onClick={addFinanceItem}
                >
                  Создать предложение
                </button>
              </div>
            </section>
            <section className="finance-grid">
              <article className="card">
                <h2>ГПР: план / факт</h2>
                <div className="finance-list">
                  {finance?.baselines.map((item) => (
                    <div key={item.id}>
                      <span>
                        <strong>{item.name}</strong>
                        <small>Версия {item.version}</small>
                      </span>
                      <b>{item.status}</b>
                      {item.status === "draft" && (
                        <button
                          onClick={() =>
                            confirmFinance("baselines", item.id, "approved")
                          }
                        >
                          Утвердить baseline
                        </button>
                      )}
                    </div>
                  ))}
                  {!finance?.baselines.length && (
                    <p className="finance-empty">Добавьте первую версию ГПР.</p>
                  )}
                </div>
                <p className="finance-warning">
                  Отстающих работ: {finance?.summary.delayed_schedule || 0}
                </p>
              </article>
              <article className="card">
                <h2>Бюджет</h2>
                <div className="finance-list">
                  {finance?.budget.map((item) => (
                    <div key={item.id}>
                      <span>
                        <strong>{item.description}</strong>
                        <small>
                          {item.category} · план {money(item.planned_amount)} ·
                          прогноз {money(item.forecast_amount)}
                        </small>
                      </span>
                      <b>{item.status}</b>
                      {item.status === "proposed" && (
                        <button
                          onClick={() =>
                            confirmFinance("budget", item.id, "approved")
                          }
                        >
                          Подтвердить
                        </button>
                      )}
                    </div>
                  ))}
                  {!finance?.budget.length && (
                    <p className="finance-empty">Строк бюджета пока нет.</p>
                  )}
                </div>
              </article>
              <article className="card">
                <h2>ДДС</h2>
                <div className="finance-list">
                  {finance?.cash_flow.map((item) => (
                    <div key={item.id}>
                      <span>
                        <strong>{item.title}</strong>
                        <small>
                          {item.direction === "inflow"
                            ? "Поступление"
                            : "Выплата"}{" "}
                          · {item.planned_date} · {money(item.planned_amount)}
                        </small>
                      </span>
                      <b>{item.status}</b>
                      {item.status === "proposed" && (
                        <button
                          onClick={() =>
                            confirmFinance("cash-flow", item.id, "approved")
                          }
                        >
                          Подтвердить
                        </button>
                      )}
                    </div>
                  ))}
                  {!finance?.cash_flow.length && (
                    <p className="finance-empty">План ДДС пока пуст.</p>
                  )}
                </div>
              </article>
              <article className="card">
                <h2>Закупки и поставки</h2>
                <div className="finance-list">
                  {finance?.procurement.map((item) => (
                    <div key={item.id}>
                      <span>
                        <strong>{item.title}</strong>
                        <small>
                          {item.supplier || "Поставщик не указан"} ·{" "}
                          {item.planned_delivery || "без срока"} ·{" "}
                          {money(item.planned_amount)}
                        </small>
                      </span>
                      <b>{item.stage}</b>
                      {item.stage === "request" && (
                        <button
                          onClick={() =>
                            confirmFinance("procurement", item.id, "ordered")
                          }
                        >
                          Заказано
                        </button>
                      )}
                    </div>
                  ))}
                  {!finance?.procurement.length && (
                    <p className="finance-empty">Закупок пока нет.</p>
                  )}
                </div>
                <p className="finance-warning">
                  Просроченных поставок:{" "}
                  {finance?.summary.late_procurement || 0}
                </p>
              </article>
              <article className="card">
                <h2>Акты и закрытие</h2>
                <div className="finance-list">
                  {finance?.acts.map((item) => (
                    <div key={item.id}>
                      <span>
                        <strong>
                          №{item.number} · {item.title}
                        </strong>
                        <small>
                          {item.act_date || "без даты"} · {money(item.amount)}
                        </small>
                      </span>
                      <b>{item.status}</b>
                      {item.status === "proposed" && (
                        <button
                          onClick={() =>
                            confirmFinance("acts", item.id, "approved")
                          }
                        >
                          Подтвердить
                        </button>
                      )}
                    </div>
                  ))}
                  {!finance?.acts.length && (
                    <p className="finance-empty">Актов пока нет.</p>
                  )}
                </div>
              </article>
              <article className="card finance-forecast">
                <h2>Прогноз</h2>
                <p
                  className={
                    (finance?.summary.cash_gap || 0) < 0 ? "bad" : "good"
                  }
                >
                  {(finance?.summary.cash_gap || 0) < 0
                    ? `Ожидаемый кассовый разрыв ${money(finance?.summary.cash_gap)}${finance?.summary.cash_gap_date ? ` к ${finance.summary.cash_gap_date}` : ""}`
                    : "Кассовый разрыв по подтверждённому плану не выявлен"}
                </p>
                <p>
                  Ожидают обработки актов: {finance?.summary.acts_pending || 0}
                </p>
                <p>
                  Прогноз бюджета: {money(finance?.summary.budget_forecast)}
                </p>
              </article>
            </section>
          </div>
        </section>
      )}
      {active === "Обязательства" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page">
            <section className="card management-intro">
              <div>
                <h2>Реестр обязательств</h2>
                <p>
                  Обязательства выделяются из документов, сообщений и
                  протоколов. Каждый вывод хранит источник и требует
                  подтверждения.
                </p>
              </div>
              <span>
                {
                  obligations.filter(
                    (item) => !["fulfilled", "dismissed"].includes(item.status),
                  ).length
                }{" "}
                открыто
              </span>
            </section>
            <section className="card management-list">
              {obligations.map((item) => (
                <article key={item.id}>
                  <div>
                    <span className={`management-status ${item.status}`}>
                      {item.status}
                    </span>
                    <h3>{item.title}</h3>
                    <p>
                      {item.source_name} · уверенность{" "}
                      {Math.round(item.confidence * 100)}%
                    </p>
                    <small>{item.source_excerpt}</small>
                  </div>
                  <div className="management-meta">
                    <strong>
                      {item.due_date
                        ? `до ${item.due_date}`
                        : "срок не определён"}
                    </strong>
                    {item.status === "needs_confirmation" && (
                      <button
                        onClick={() => updateObligation(item, "confirmed")}
                      >
                        Подтвердить
                      </button>
                    )}
                    {item.status === "confirmed" && (
                      <button
                        onClick={() => updateObligation(item, "in_progress")}
                      >
                        В работу
                      </button>
                    )}
                    {!["fulfilled", "dismissed"].includes(item.status) && (
                      <button
                        className="complete"
                        onClick={() => updateObligation(item, "fulfilled")}
                      >
                        Исполнено
                      </button>
                    )}
                  </div>
                </article>
              ))}
              {!obligations.length && (
                <div className="empty">
                  <ClipboardCheck />
                  <p>
                    Обязательства появятся после анализа нового документа,
                    сообщения или протокола.
                  </p>
                </div>
              )}
            </section>
          </div>
        </section>
      )}
      {active === "Совещания" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page">
            <section className="card meeting-create">
              <div>
                <h2>Новое совещание</h2>
                <p>
                  После встречи внесите протокол — система выделит поручения,
                  риски и решения.
                </p>
              </div>
              <div>
                <input
                  value={newMeetingTitle}
                  onChange={(e) => setNewMeetingTitle(e.target.value)}
                  placeholder="Название совещания"
                />
                <input
                  type="datetime-local"
                  value={newMeetingDate}
                  onChange={(e) => setNewMeetingDate(e.target.value)}
                />
                <textarea
                  value={newMeetingAgenda}
                  onChange={(e) => setNewMeetingAgenda(e.target.value)}
                  placeholder="Повестка"
                />
                <button
                  disabled={!newMeetingTitle.trim()}
                  onClick={createMeeting}
                >
                  Запланировать
                </button>
              </div>
            </section>
            <section className="meeting-grid">
              {meetings.map((item) => (
                <article className="card meeting-card" key={item.id}>
                  <span className={`management-status ${item.status}`}>
                    {item.status}
                  </span>
                  <h2>{item.title}</h2>
                  <p>
                    {item.scheduled_at
                      ? new Date(item.scheduled_at).toLocaleString("ru-RU")
                      : "Дата не назначена"}
                  </p>
                  {item.agenda && (
                    <div className="meeting-agenda">
                      <strong>Повестка</strong>
                      <p>{item.agenda}</p>
                    </div>
                  )}
                  {item.minutes && (
                    <div className="meeting-agenda">
                      <strong>Протокол</strong>
                      <p>{item.minutes}</p>
                    </div>
                  )}
                  {item.status !== "completed" &&
                    item.status !== "cancelled" && (
                      <button onClick={() => recordMinutes(item)}>
                        Внести протокол и проанализировать
                      </button>
                    )}
                </article>
              ))}
              {!meetings.length && (
                <div className="card empty">
                  <Users />
                  <p>Совещаний пока нет.</p>
                </div>
              )}
            </section>
          </div>
        </section>
      )}
      {active === "Уведомления" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page">
            <section className="card management-intro">
              <div>
                <h2>Центр уведомлений</h2>
                <p>Просроченные и ближайшие сроки, открытые риски и решения.</p>
              </div>
              <button onClick={refreshNotifications}>Обновить контроль</button>
            </section>
            <section className="card notification-list">
              {notifications.map((item) => (
                <article className={item.is_read ? "read" : ""} key={item.id}>
                  <div className={`notification-kind ${item.kind}`}>
                    <Bell />
                  </div>
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.body}</p>
                    <small>
                      {new Date(item.created_at).toLocaleString("ru-RU")}
                    </small>
                  </div>
                  {!item.is_read && (
                    <button onClick={() => markNotification(item)}>
                      Прочитано
                    </button>
                  )}
                </article>
              ))}
              {!notifications.length && (
                <div className="empty">
                  <Bell />
                  <p>
                    Нажмите «Обновить контроль», чтобы собрать актуальные
                    уведомления.
                  </p>
                </div>
              )}
            </section>
          </div>
        </section>
      )}
      {active === "Договоры" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page contracts-page">
            <section className="card contract-create">
              <div>
                <h2>Добавить договор</h2>
                <p>
                  Договор становится юридическим якорем документов, задач и
                  решений проекта.
                </p>
              </div>
              <div className="contract-form">
                <input
                  value={newContractNumber}
                  onChange={(e) => setNewContractNumber(e.target.value)}
                  placeholder="Номер договора"
                />
                <input
                  value={newContractTitle}
                  onChange={(e) => setNewContractTitle(e.target.value)}
                  placeholder="Название"
                />
                <input
                  value={newCounterparty}
                  onChange={(e) => setNewCounterparty(e.target.value)}
                  placeholder="Контрагент"
                />
                <button
                  disabled={
                    !newContractNumber.trim() || !newContractTitle.trim()
                  }
                  onClick={createContract}
                >
                  Добавить
                </button>
              </div>
            </section>
            <section className="contract-list">
              {contracts
                .filter(
                  (item) =>
                    !query ||
                    `${item.number} ${item.title} ${item.counterparty || ""}`
                      .toLocaleLowerCase("ru-RU")
                      .includes(query.toLocaleLowerCase("ru-RU")),
                )
                .map((item) => (
                  <article className="card contract-card" key={item.id}>
                    <div className="contract-number">{item.number}</div>
                    <div>
                      <span className={`contract-status ${item.status}`}>
                        {item.status}
                      </span>
                      <h2>{item.title}</h2>
                      <p>
                        {item.counterparty || "Контрагент не указан"}
                        {item.signed_at
                          ? ` · от ${new Date(item.signed_at).toLocaleDateString("ru-RU")}`
                          : ""}
                      </p>
                      {item.notes && <small>{item.notes}</small>}
                    </div>
                    <div className="contract-links">
                      <span>Документ-источник</span>
                      <strong>
                        {item.source_document_id
                          ? `№${item.source_document_id}`
                          : "не привязан"}
                      </strong>
                    </div>
                  </article>
                ))}
              {!contracts.length && (
                <div className="card empty">
                  <FileText />
                  <p>В проекте пока нет договоров.</p>
                </div>
              )}
            </section>
          </div>
        </section>
      )}
      {active === "Предложения" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page">
            <section className="card proposal-intro">
              <div>
                <h2>Предлагаемые изменения</h2>
                <p>
                  Пакеты «Безопасная копия» не затрагивают оригиналы. В пакетах
                  «Оригинал» можно применить только одно явно выбранное
                  изменение.
                </p>
              </div>
              <span>{proposals.length} пакетов</span>
            </section>
            <div className="proposal-list">
              {proposals.map((proposal) => (
                <article className="card proposal-card" key={proposal.id}>
                  <div className="proposal-head">
                    <div>
                      <span className={`proposal-status ${proposal.status}`}>
                        {proposal.status}
                      </span>
                      <h2>{proposal.folder_name}</h2>
                      <p>
                        Пакет №{proposal.id} · действий{" "}
                        {proposal.actions.length} ·{" "}
                        <strong>
                          {proposal.copy_folder_id.startsWith("virtual:")
                            ? "ОРИГИНАЛ — только одно подтверждённое изменение"
                            : "БЕЗОПАСНАЯ КОПИЯ"}
                        </strong>
                      </p>
                    </div>
                    <div className="proposal-controls">
                      {proposal.status === "waiting_confirmation" &&
                        !proposal.copy_folder_id.startsWith("virtual:") && (
                          <button
                            disabled={busyProposal === proposal.id}
                            onClick={() => approveSafe(proposal)}
                          >
                            Подтвердить только безопасные
                          </button>
                        )}
                      {["approved", "ready_to_apply_to_copy"].includes(
                        proposal.status,
                      ) &&
                        !proposal.copy_folder_id.startsWith("virtual:") && (
                          <button
                            className="apply-safe"
                            disabled={busyProposal === proposal.id}
                            onClick={() => applyProposal(proposal)}
                          >
                            Dry-run и применить к копии
                          </button>
                        )}
                      {["applied", "rollback_partial"].includes(
                        proposal.status,
                      ) && (
                        <button
                          className="rollback"
                          disabled={busyProposal === proposal.id}
                          onClick={() => rollbackProposal(proposal)}
                        >
                          <RotateCcw />
                          Откатить
                        </button>
                      )}
                    </div>
                  </div>
                  {proposal.note && (
                    <div className="proposal-note">{proposal.note}</div>
                  )}
                  <div className="proposal-actions-table">
                    <div className="proposal-table-head">
                      <span>Решение</span>
                      <span>Было</span>
                      <span>Станет</span>
                      <span>Уверенность и основание</span>
                    </div>
                    {proposal.actions.slice(0, 500).map((action) => (
                      <div className="proposal-action" key={action.id}>
                        <select
                          value={action.user_decision}
                          onChange={(e) =>
                            editProposalAction(action, e.target.value)
                          }
                          disabled={proposal.status !== "waiting_confirmation"}
                        >
                          <option value="pending">Проверить</option>
                          <option value="approved">Одобрить</option>
                          <option value="edited">Изменить</option>
                          <option value="skipped">Пропустить</option>
                        </select>
                        <strong>{action.source}</strong>
                        <div>
                          <input
                            value={action.edited_name || action.proposed_name}
                            disabled={
                              proposal.status !== "waiting_confirmation"
                            }
                            onBlur={() =>
                              action.user_decision === "edited" &&
                              saveProposalAction(proposal.id, action.id)
                            }
                            onChange={(e) =>
                              setProposals((rows) =>
                                rows.map((row) =>
                                  row.id === proposal.id
                                    ? {
                                        ...row,
                                        actions: row.actions.map((x) =>
                                          x.id === action.id
                                            ? {
                                                ...x,
                                                edited_name: e.target.value,
                                                user_decision: "edited",
                                              }
                                            : x,
                                        ),
                                      }
                                    : row,
                                ),
                              )
                            }
                          />
                          <select
                            value={action.edited_folder || action.target_folder}
                            disabled={
                              proposal.status !== "waiting_confirmation"
                            }
                            onChange={(e) =>
                              setProposals((rows) =>
                                rows.map((row) =>
                                  row.id === proposal.id
                                    ? {
                                        ...row,
                                        actions: row.actions.map((x) =>
                                          x.id === action.id
                                            ? {
                                                ...x,
                                                edited_folder: e.target.value,
                                                user_decision: "edited",
                                              }
                                            : x,
                                        ),
                                      }
                                    : row,
                                ),
                              )
                            }
                          >
                            {targetFolders.map((folder) => (
                              <option key={folder}>{folder}</option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <span
                            className={
                              action.special_case ? "needs-review" : ""
                            }
                          >
                            {Math.round(action.confidence * 100)}%
                            {action.special_case
                              ? ` · ${action.special_case}`
                              : ""}
                          </span>
                          <p>{action.reasoning}</p>
                          {proposal.copy_folder_id.startsWith("virtual:") &&
                            action.user_decision === "edited" && (
                              <button
                                className="apply-source-inline"
                                disabled={busyProposal === proposal.id}
                                onClick={() =>
                                  applyOneToSource(proposal, action)
                                }
                              >
                                Проверить и изменить этот оригинал
                              </button>
                            )}
                        </div>
                      </div>
                    ))}
                  </div>
                </article>
              ))}
              {!proposals.length && (
                <div className="card empty">
                  <GitPullRequest />
                  <p>
                    Предложений пока нет. Запустите анализ подготовленного
                    снимка папки.
                  </p>
                </div>
              )}
            </div>
          </div>
        </section>
      )}
      {active === "Интеграции" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page">
            <div className="integration-grid">
              {[
                [
                  "Google Drive",
                  "Документы и рабочие папки",
                  Boolean(googleState?.authorized),
                ],
                [
                  "Google Tasks",
                  "Подтверждённые задачи",
                  Boolean(googleState?.tasks_authorized),
                ],
                [
                  "Google Calendar",
                  "Сроки и события проекта",
                  Boolean(googleState?.calendar_authorized),
                ],
                [
                  "Gmail",
                  "Разрешённые письма и подтверждаемая отправка",
                  Boolean(googleState?.gmail_authorized),
                ],
                [
                  "Telegram",
                  "Файлы, сообщения и уведомления",
                  Boolean(systemState?.telegram_ready),
                ],
              ].map(([name, description, connected]) => (
                <article className="card integration-card" key={String(name)}>
                  <div
                    className={`integration-icon ${connected ? "connected" : ""}`}
                  >
                    {name === "Telegram" ? <Bot /> : <CalendarDays />}
                  </div>
                  <div>
                    <h2>{name}</h2>
                    <p>{description}</p>
                  </div>
                  <span className={connected ? "connected" : ""}>
                    {connected ? "Подключено" : "Не подключено"}
                  </span>
                  {name === "Gmail" && connected ? (
                    <button onClick={() => syncGmail()} disabled={gmailSyncing}>
                      {gmailSyncing ? "Получаю…" : "Получить письма"}
                    </button>
                  ) : (
                    name !== "Telegram" && (
                      <button onClick={connectGoogle}>
                        {connected ? "Переподключить" : "Подключить"}
                      </button>
                    )
                  )}
                  {name === "Gmail" && gmailSyncStatus && (
                    <div className="integration-sync-result">
                      <small>{gmailSyncStatus}</small>
                      {!gmailSyncing && (
                        <button onClick={openGmailResults}>
                          Открыть AI Secretary
                        </button>
                      )}
                    </div>
                  )}
                </article>
              ))}
            </div>
            <section className="card system-checks">
              <div className="card-head">
                <div>
                  <h2>Состояние системы</h2>
                  <p>Проверка обязательных и внешних компонентов</p>
                </div>
                <button onClick={loadIntegrations}>Проверить снова</button>
              </div>
              {Object.entries(systemState?.checks || {}).map(
                ([name, check]) => (
                  <div className="check-row" key={name}>
                    <span className={check.ok ? "ok" : "bad"}></span>
                    <strong>{name.replaceAll("_", " ")}</strong>
                    <p>{check.message}</p>
                    <small>
                      {check.required ? "обязательно" : "интеграция"}
                    </small>
                  </div>
                ),
              )}
            </section>
          </div>
        </section>
      )}
      {active === "Журнал" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page">
            <section className="card">
              <div className="card-head">
                <div>
                  <h2>Журнал действий</h2>
                  <p>Последние {auditLogs.length} зафиксированных операций</p>
                </div>
                <button onClick={load}>Обновить</button>
              </div>
              <div className="audit-list">
                {auditLogs
                  .filter(
                    (item) =>
                      !query ||
                      `${item.action} ${item.entity_type} ${item.details || ""}`
                        .toLocaleLowerCase("ru-RU")
                        .includes(query.toLocaleLowerCase("ru-RU")),
                  )
                  .map((item) => (
                    <article key={item.id}>
                      <div className="audit-dot"></div>
                      <div>
                        <strong>{item.action}</strong>
                        <p>
                          {item.entity_type}
                          {item.entity_id ? ` №${item.entity_id}` : ""}
                        </p>
                        {item.details && <small>{item.details}</small>}
                      </div>
                      <time>
                        {new Date(item.created_at).toLocaleString("ru-RU")}
                      </time>
                    </article>
                  ))}
                {!auditLogs.length && (
                  <div className="empty">
                    <ShieldCheck />
                    <p>
                      Записей журнала пока нет или доступ разрешён только
                      администратору.
                    </p>
                  </div>
                )}
              </div>
            </section>
          </div>
        </section>
      )}
      {active === "Настройки" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page settings-grid">
            <section className="card profile-settings">
              <span className="eyebrow">ПРОФИЛЬ</span>
              <div className="settings-profile">
                <div className="avatar">
                  {currentUser?.name?.slice(0, 1) || "D"}
                </div>
                <div>
                  <h2>{currentUser?.name || "Пользователь"}</h2>
                  <p>{currentUser?.email}</p>
                </div>
              </div>
              <div className="setting-row">
                <span>Роль в системе</span>
                <strong>
                  {currentUser?.is_admin ? "Администратор" : "Пользователь"}
                </strong>
              </div>
              <div className="setting-row">
                <span>Активный проект</span>
                <strong>
                  {projects.find((item) => item.id === projectId)?.name}
                </strong>
              </div>
            </section>
            <section className="card">
              <div className="card-head">
                <div>
                  <h2>Участники проекта</h2>
                  <p>Доступ к выбранному проекту</p>
                </div>
              </div>
              <div className="member-list">
                {members.map((member) => (
                  <article key={member.membership_id}>
                    <div className="avatar">{member.name.slice(0, 1)}</div>
                    <div>
                      <strong>{member.name}</strong>
                      <p>{member.email}</p>
                    </div>
                    <span>{member.role}</span>
                  </article>
                ))}
              </div>
            </section>
            <section className="card span-settings">
              <div className="card-head">
                <div>
                  <h2>AI и защита данных</h2>
                  <p>Что разрешено передавать внешней модели для выбранного проекта</p>
                </div>
              </div>
              {aiPolicy && (
                <div className="form-grid">
                  <label>
                    Режим обработки
                    <select
                      value={aiPolicy.mode}
                      onChange={(event) =>
                        setAiPolicy({
                          ...aiPolicy,
                          mode: event.target.value as AIProjectPolicy["mode"],
                        })
                      }
                    >
                      <option value="local_only">Только локально — внешний AI запрещён</option>
                      <option value="redacted">С обезличиванием — рекомендовано</option>
                      <option value="metadata_only">Только метаданные</option>
                      <option value="external_allowed">Полный текст разрешён</option>
                    </select>
                  </label>
                  <label className="setting-row">
                    <span>Проверять персональные данные перед отправкой</span>
                    <input
                      type="checkbox"
                      checked={aiPolicy.dlp_enabled}
                      onChange={(event) =>
                        setAiPolicy({ ...aiPolicy, dlp_enabled: event.target.checked })
                      }
                    />
                  </label>
                  <button onClick={saveAIPolicy}>Сохранить политику</button>
                  <p>Версия правил и промпта: {aiPolicy.prompt_version}</p>
                </div>
              )}
            </section>
            <section className="card span-settings">
              <h2>Принципы безопасности</h2>
              <div className="safety-list">
                <p>
                  <ShieldCheck /> Оригиналы файлов никогда не перемещаются и не
                  изменяются.
                </p>
                <p>
                  <ShieldCheck /> Черновики ответов не отправляются без
                  подтверждения.
                </p>
                <p>
                  <ShieldCheck /> Изменения проекта фиксируются в журнале.
                </p>
              </div>
            </section>
            <section className="card span-settings">
              <div className="card-head">
                <div>
                  <h2>Очередь массовой обработки</h2>
                  <p>Прогресс, ошибки и операции, требующие диагностики</p>
                </div>
              </div>
              {processingQueue && (
                <div className="safety-list">
                  <p>
                    Активно: <strong>{processingQueue.summary.active}</strong> · Ошибок: {" "}
                    <strong>{processingQueue.summary.failed}</strong> · Dead-letter: {" "}
                    <strong>{processingQueue.summary.dead_letter}</strong>
                  </p>
                  {processingQueue.snapshots
                    .filter((item) => item.status === "failed")
                    .map((item) => (
                      <p key={`snapshot-${item.id}`}>
                        Снимок №{item.id}: {item.error || "ошибка без описания"}
                        <button onClick={() => retrySnapshot(item.id)}>Повторить</button>
                      </p>
                    ))}
                  {processingQueue.sessions
                    .filter((item) => item.status === "failed")
                    .map((item) => (
                      <p key={`session-${item.id}`}>
                        Обработка №{item.id}: {item.error_message || "ошибка без описания"}
                        <button onClick={() => retryOrganizerSession(item.id)}>Повторить</button>
                      </p>
                    ))}
                </div>
              )}
            </section>
          </div>
        </section>
      )}
      {(active === "AI Secretary" || active === "Письма") && (
        <section
          className={`secretary-overlay ${collapsed ? "collapsed" : ""}`}
        >
          <div className="secretary-page">
            <section className="card secretary-intro">
              <div className="source-icon">
                {active === "Письма" ? <Mail /> : <Bot />}
              </div>
              <div>
                <h2>{active === "Письма" ? "Входящие письма" : "Входящие AI Secretary"}</h2>
                <p>
                  {active === "Письма"
                    ? "Письма из Gmail с исходным текстом, AI-сводкой, задачами и черновиками ответов."
                    : "Источник → контекст проекта и договора → сводка → предложения задач и ответа. Ничего внешнего не создаётся без подтверждения."}
                </p>
              </div>
              <span>
                {
                  inbox.filter(
                    (item) =>
                      !item.context_confirmed ||
                      item.tasks.some(
                        (task) => task.external_action_status === "proposed",
                      ),
                  ).length
                }{" "}
                требуют внимания
              </span>
              {active === "Письма" && (
                <button className="inbox-sync" onClick={() => syncGmail()} disabled={gmailSyncing}>
                  <RefreshCw /> {gmailSyncing ? "Получаю…" : "Получить новые"}
                </button>
              )}
            </section>
            {active === "AI Secretary" && <section className="card inbox-compose">
              <h2>Добавить тестовое сообщение</h2>
              <p>Вставьте письмо или сообщение для полного сценария MVP2.</p>
              <input
                value={incomingName}
                onChange={(e) => setIncomingName(e.target.value)}
                placeholder="Название или отправитель"
              />
              <textarea
                value={incomingText}
                onChange={(e) => setIncomingText(e.target.value)}
                placeholder="Текст входящего сообщения"
              />
              <button
                disabled={!incomingName.trim() || !incomingText.trim()}
                onClick={ingestMessage}
              >
                Проанализировать
              </button>
            </section>}
            <section className="inbox-list">
              <div className="inbox-toolbar">
                <div>
                  <strong>Входящие письма и сообщения</strong>
                  <small>{inbox.length} писем и сообщений обработано</small>
                </div>
                <span>Нажмите на письмо, чтобы открыть анализ</span>
              </div>
              <div className="inbox-filters">
                {[
                  ["all", "Все", inboxCounts.all],
                  ["attention", "Требуют внимания", inboxCounts.attention],
                  ["tasks", "Есть задачи", inboxCounts.tasks],
                  ["drafts", "Есть черновики", inboxCounts.drafts],
                ].map(([value, label, count]) => (
                  <button className={inboxFilter === value ? "selected" : ""} onClick={() => setInboxFilter(String(value))} key={value}>
                    {label} <b>{count}</b>
                  </button>
                ))}
              </div>
              {visibleInbox
                .map((message) => {
                  const expanded = expandedInboxId === message.id;
                  return (
                  <article className={`card inbox-card ${expanded ? "expanded" : "collapsed"}`} key={message.id}>
                    <div className="inbox-head">
                      <div>
                        <span className={`draft-status ${message.status}`}>
                          {message.status === "completed"
                            ? "Обработано"
                            : message.status === "in_progress"
                              ? "В работе"
                              : message.context_confirmed
                                ? "Новое"
                                : "Подтвердите контекст"}
                        </span>
                        <h2>{message.source_name}</h2>
                        <div className="inbox-meta">
                          <span>{message.source_sender || (message.source_type === "email" ? "Отправитель не указан" : message.source_type)}</span>
                          <time>{new Date(message.created_at).toLocaleString("ru-RU")}</time>
                        </div>
                        <p>
                          {message.source_type} · уверенность связи{" "}
                          {Math.round(message.context_confidence * 100)}% ·{" "}
                          {message.context_evidence}
                        </p>
                        {message.source_url && (
                          <a
                            href={message.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Открыть первоисточник
                          </a>
                        )}
                      </div>
                      <button className="inbox-toggle" onClick={() => setExpandedInboxId(expanded ? null : message.id)}>
                        {expanded ? "Свернуть" : "Открыть"}
                      </button>
                      {message.status !== "completed" ? (
                        <button className="inbox-workflow" onClick={() => updateInboxStatus(message, message.status === "in_progress" ? "completed" : "in_progress")}>
                          {message.status === "in_progress" ? "Завершить" : "В работу"}
                        </button>
                      ) : (
                        <button className="inbox-workflow secondary" onClick={() => updateInboxStatus(message, "in_progress")}>Вернуть</button>
                      )}
                      {expanded && !message.context_confirmed && (
                        <div className="context-confirm">
                          <select
                            value={message.contract_id || ""}
                            onChange={(e) =>
                              setInbox((rows) =>
                                rows.map((row) =>
                                  row.id === message.id
                                    ? {
                                        ...row,
                                        contract_id:
                                          Number(e.target.value) || undefined,
                                      }
                                    : row,
                                ),
                              )
                            }
                          >
                            <option value="">Без договора</option>
                            {contracts.map((contract) => (
                              <option value={contract.id} key={contract.id}>
                                {contract.number} — {contract.title}
                              </option>
                            ))}
                          </select>
                          <button
                            onClick={() => confirmMessageContext(message)}
                          >
                            Подтвердить связь
                          </button>
                        </div>
                      )}
                    </div>
                    {!expanded && <p className="inbox-preview">{message.summary.replace(/\s+/g, " ").slice(0, 220)}</p>}
                    {expanded && <>
                    <details className="inbox-original">
                      <summary>Исходный текст письма</summary>
                      <pre>{message.content}</pre>
                    </details>
                    {message.attachments.length > 0 && (
                      <div className="inbox-attachments">
                        <h3>Вложения ({message.attachments.length})</h3>
                        {message.attachments.map((attachment, index) => (
                          <div key={`${attachment.name}-${index}`}>
                            <FileText />
                            <span><strong>{attachment.name}</strong><small>{attachment.mime_type} · {attachment.size ? `${Math.ceil(attachment.size / 1024)} КБ` : "размер не указан"}</small></span>
                          </div>
                        ))}
                        {message.source_url && <a href={message.source_url} target="_blank" rel="noreferrer">Открыть письмо и скачать вложения</a>}
                      </div>
                    )}
                    <pre className="inbox-summary">{message.summary}</pre>
                    {message.risks.length > 0 && (
                      <div className="inbox-risks">
                        <h3>Риски и контроль</h3>
                        {message.risks.map((risk) => (
                          <div key={risk.id}>
                            <AlertTriangle />
                            <span>
                              <strong>{risk.title}</strong>
                              <small>{risk.source_excerpt}</small>
                            </span>
                            <b>
                              {risk.criticality} ·{" "}
                              {Math.round(risk.confidence * 100)}%
                            </b>
                          </div>
                        ))}
                      </div>
                    )}
                    {message.tasks.length > 0 && (
                      <div className="inbox-proposals">
                        <h3>Предложенные задачи</h3>
                        {message.tasks.map((task) => (
                          <div className="inbox-task" key={task.id}>
                            <div>
                              <strong>{task.title}</strong>
                              <p>
                                {task.due_date
                                  ? `Срок ${task.due_date}`
                                  : "Срок не найден"}{" "}
                                · уверенность{" "}
                                {Math.round(task.confidence * 100)}%
                              </p>
                            </div>
                            <span>
                              {task.external_action_status === "executed"
                                ? "Создано в Google"
                                : task.external_action_status === "failed"
                                  ? "Ошибка создания — можно повторить"
                                  : "Только предложение"}
                            </span>
                            {task.external_action_status !== "executed" && (
                              <button
                                disabled={!message.context_confirmed}
                                onClick={() => approveExternal(task)}
                              >
                                {task.external_action_status === "failed"
                                  ? "Повторить"
                                  : "Подтвердить и создать"}
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {message.drafts.map((draft) => (
                      <div className="inbox-draft" key={draft.id}>
                        <h3>{draft.subject}</h3>
                        <textarea
                          value={draft.body}
                          disabled={draft.status !== "draft"}
                          onChange={(e) =>
                            setInbox((rows) =>
                              rows.map((row) =>
                                row.id === message.id
                                  ? {
                                      ...row,
                                      drafts: row.drafts.map((item) =>
                                        item.id === draft.id
                                          ? { ...item, body: e.target.value }
                                          : item,
                                      ),
                                    }
                                  : row,
                              ),
                            )
                          }
                        />
                        <span>
                          {draft.status === "sent"
                            ? "Отправлен через Gmail"
                            : draft.status === "approved"
                            ? "Подтверждён — не отправлен"
                            : draft.status === "rejected"
                              ? "Отклонён"
                              : "Черновик — не отправлен"}
                        </span>
                        {draft.status === "draft" && (
                          <div className="draft-actions">
                            <button
                              onClick={() =>
                                reviewInboxDraft(message.id, draft, "rejected")
                              }
                            >
                              Отклонить
                            </button>
                            <button
                              className="approve"
                              onClick={() =>
                                reviewInboxDraft(message.id, draft, "approved")
                              }
                            >
                              Подтвердить черновик
                            </button>
                          </div>
                        )}
                        {draft.status === "approved" &&
                          message.source_type === "email" && (
                            <div className="draft-actions">
                              <button
                                className="approve"
                                onClick={() => sendGmailDraft(draft)}
                              >
                                Отправить через Gmail
                              </button>
                            </div>
                          )}
                      </div>
                    ))}
                    </>}
                  </article>
                  );
                })}
              {!visibleInbox.length && (
                <div className="card empty">
                  <Bot />
                  <p>{inbox.length ? "По выбранному фильтру писем нет." : "Входящих пока нет. Получите письма из Gmail или добавьте сообщение."}</p>
                </div>
              )}
            </section>
          </div>
        </section>
      )}
      {active === "Проекты" && (
        <section className={`projects-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="projects-page">
            <section className="card project-create">
              <div>
                <h2>Новый проект</h2>
                <p>
                  Создайте отдельное рабочее пространство для следующего
                  направления.
                </p>
              </div>
              <div>
                <input
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && createProject()}
                  placeholder="Название проекта"
                />
                <button
                  disabled={!newProjectName.trim()}
                  onClick={createProject}
                >
                  Создать проект
                </button>
              </div>
            </section>
            <section className="project-cards">
              {projects
                .filter(
                  (item) =>
                    !query ||
                    item.name
                      .toLocaleLowerCase("ru-RU")
                      .includes(query.toLocaleLowerCase("ru-RU")),
                )
                .map((item) => {
                  const stats = projectStats[item.id];
                  return (
                    <article
                      className={`card project-card ${item.id === projectId ? "current" : ""}`}
                      key={item.id}
                    >
                      <div className="project-title">
                        <div className="source-icon">
                          <FolderKanban />
                        </div>
                        <div>
                          <span className="eyebrow">
                            ПРОЕКТ {item.id === projectId ? "· ТЕКУЩИЙ" : ""}
                          </span>
                          <h2>{item.name}</h2>
                        </div>
                      </div>
                      <div className="project-metrics">
                        <span>
                          Документы<strong>{stats?.documents || 0}</strong>
                        </span>
                        <span>
                          Задачи<strong>{stats?.open_tasks || 0}</strong>
                        </span>
                        <span>
                          Риски<strong>{stats?.open_risks || 0}</strong>
                        </span>
                        <span>
                          Внимание<strong>{stats?.attention || 0}</strong>
                        </span>
                      </div>
                      <button
                        onClick={() => {
                          setProjectId(item.id);
                          setActive("Рабочий центр");
                        }}
                      >
                        {item.id === projectId
                          ? "Открыть рабочий центр"
                          : "Переключиться на проект"}
                      </button>
                    </article>
                  );
                })}
            </section>
          </div>
        </section>
      )}
      {active === "Документы" && (
        <section
          className={`documents-overlay ${collapsed ? "collapsed" : ""}`}
        >
          <div className="documents-layout">
            <div className="card">
              <div className="card-head">
                <div>
                  <h2>Реестр документов</h2>
                  <p>Найдено: {visibleDocuments.length}</p>
                </div>
              </div>
              <div className="document-register">
                {visibleDocuments.map((item) => (
                  <button
                    className={
                      selectedDocument?.id === item.id ? "selected" : ""
                    }
                    onClick={() => openDocument(item)}
                    key={item.id}
                  >
                    <FileText />
                    <span>
                      <strong>{item.name}</strong>
                      <small>
                        {item.source} · версия {item.current_version || 1} ·{" "}
                        {item.status}
                      </small>
                    </span>
                  </button>
                ))}
                {!visibleDocuments.length && (
                  <div className="empty">
                    <FileText />
                    <p>Документы не найдены</p>
                  </div>
                )}
              </div>
            </div>
            <div className="card document-detail">
              {selectedDocument ? (
                <>
                  <div className="card-head">
                    <div>
                      <h2>{selectedDocument.name}</h2>
                      <p>{selectedDocument.mime_type || "Документ"}</p>
                    </div>
                    {selectedDocument.external_id && (
                      <a
                        className="source-link"
                        href={`https://drive.google.com/open?id=${selectedDocument.external_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Открыть оригинал
                      </a>
                    )}
                  </div>
                  <div className="document-links">
                    <span>
                      Задачи <strong>{selectedDocument.links.tasks}</strong>
                    </span>
                    <span>
                      Риски <strong>{selectedDocument.links.risks}</strong>
                    </span>
                    <span>
                      Решения{" "}
                      <strong>{selectedDocument.links.decisions}</strong>
                    </span>
                    <span>
                      Черновики <strong>{selectedDocument.links.drafts}</strong>
                    </span>
                  </div>
                  <h3>Краткая сводка</h3>
                  <p className="document-summary">
                    {selectedDocument.summary ||
                      "Сводка появится после анализа содержимого."}
                  </p>
                  <p className="versions">
                    Версий: {selectedDocument.versions.length || 1}
                  </p>
                </>
              ) : (
                <div className="empty">
                  <FileText />
                  <p>Выберите документ слева</p>
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
