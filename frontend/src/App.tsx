import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { Login } from "./auth/Login";
import { useProjectSelection } from "./context/useProjectSelection";
import { useFinanceController } from "./modules/finance/useFinanceController";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Bot,
  CalendarDays,
  ChevronLeft,
  ClipboardCheck,
  Download,
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
  Archive,
  Trash2,
} from "lucide-react";

type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

type Project = { id: number; name: string; archived_at?: string };
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
type ContractSourceCandidate = {
  document_id: number;
  name: string;
  source: string;
  mime_type?: string;
  score: number;
  reasons: string[];
  text_ready: boolean;
};
type TaskRow = {
  id: number;
  title: string;
  status: string;
  priority: string;
  due_date?: string;
  assignee_user_id: number;
  assignee_name: string;
  source_file_name: string;
  source_excerpt: string;
  confidence: number;
  needs_review: boolean;
  message_id?: number;
  external_action_status: string;
  google_task_id?: string;
  google_calendar_event_id?: string;
  result_note?: string;
  completion_document_id?: number;
  completion_document_name?: string;
};
type TaskHistoryRow = {
  action: string;
  old_status?: string;
  new_status?: string;
  result_note?: string;
  completion_document_name?: string;
  details?: string;
  changed_by: string;
  changed_at: string;
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
  analysis?: {
    source_ready: boolean;
    tasks: number;
    obligations: number;
    risks: number;
    decisions: number;
  };
};
type AnalysisResult = {
  status: string;
  mode?: string;
  documents?: number;
  tasks?: number;
  risks?: number;
  drafts?: number;
  copy_folder_name?: string;
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
type DriveBreadcrumb = { id: string; name: string };
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
  attachments: { name: string; mime_type: string; size: number; attachment_id?: string; document_id?: number; imported?: boolean }[];
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
type AutomationRule = {
  id: number;
  project_id: number;
  contract_id?: number;
  source_document_id?: number;
  name: string;
  day_of_month: number;
  recipient_to: string;
  subject_template: string;
  body_template: string;
  task_title_template: string;
  active: boolean;
  next_run_on: string;
  last_run_on?: string;
  runs: { id: number; scheduled_for: string; task_id?: number; response_draft_id?: number; status: string }[];
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
type AnalyticsDistribution = { key: string; count: number }[];
type ProjectAnalytics = {
  summary: {
    documents: number;
    document_coverage: number;
    open_tasks: number;
    overdue_tasks: number;
    open_risks: number;
    pending_decisions: number;
    contracts: number;
    active_contracts: number;
    messages: number;
    pending_messages: number;
  };
  documents_by_source: AnalyticsDistribution;
  documents_by_status: AnalyticsDistribution;
  tasks_by_status: AnalyticsDistribution;
  risks_by_criticality: AnalyticsDistribution;
  messages_by_channel: AnalyticsDistribution;
};
type IntegrationItem = {
  key: string;
  provider: string;
  capability: "storage" | "channel" | "task" | "calendar" | "ai";
  name: string;
  description: string;
  available: boolean;
  connected: boolean;
  action?: "oauth" | "sync" | "local_upload" | "ai_policy";
  detail?: string;
};

const items = [
  [LayoutDashboard, "Рабочий центр"],
  [FolderKanban, "Проекты"],
  [FileText, "Договоры"],
  [FileText, "Документы"],
  [Search, "Центр знаний"],
  [BarChart3, "Аналитика"],
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

export function App() {
  const { projectId, projectIdRef, rememberProject, initialProjectId } = useProjectSelection();
  const [ready, setReady] = useState(false),
    [collapsed, setCollapsed] = useState(false),
    [mobile, setMobile] = useState(false),
    [online, setOnline] = useState(navigator.onLine),
    [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(null);
  const [active, setActive] = useState("Рабочий центр"),
    [query, setQuery] = useState(""),
    [newProjectName, setNewProjectName] = useState(""),
    [newContractNumber, setNewContractNumber] = useState(""),
    [newContractTitle, setNewContractTitle] = useState(""),
    [newCounterparty, setNewCounterparty] = useState(""),
    [contractDocumentTabs, setContractDocumentTabs] = useState<Record<number, "recommended" | "server" | "upload" | "google">>({}),
    [contractDocumentQueries, setContractDocumentQueries] = useState<Record<number, string>>({}),
    [contractSourceCandidates, setContractSourceCandidates] = useState<Record<number, ContractSourceCandidate[]>>({}),
    [contractCandidateBusy, setContractCandidateBusy] = useState(0),
    [projectStats, setProjectStats] = useState<Record<number, ProjectStats>>(
      {},
    ),
    [taskFilter, setTaskFilter] = useState("open"),
    [tasks, setTasks] = useState<TaskRow[]>([]),
    [completionTaskId, setCompletionTaskId] = useState(0),
    [completionNote, setCompletionNote] = useState(""),
    [completionDocumentId, setCompletionDocumentId] = useState(0),
    [taskHistoryId, setTaskHistoryId] = useState(0),
    [taskHistory, setTaskHistory] = useState<TaskHistoryRow[]>([]),
    [risks, setRisks] = useState<RiskRow[]>([]),
    [decisions, setDecisions] = useState<DecisionRow[]>([]),
    [drafts, setDrafts] = useState<ResponseDraft[]>([]),
    [inbox, setInbox] = useState<InboxMessage[]>([]),
    [expandedInboxId, setExpandedInboxId] = useState<number | null>(null),
    [inboxFilter, setInboxFilter] = useState("all"),
    [selectedInboxIds, setSelectedInboxIds] = useState<number[]>([]),
    [bulkInboxProjectId, setBulkInboxProjectId] = useState(initialProjectId),
    [bulkInboxContractId, setBulkInboxContractId] = useState(0),
    [incomingName, setIncomingName] = useState(""),
    [incomingText, setIncomingText] = useState(""),
    [automationRules, setAutomationRules] = useState<AutomationRule[]>([]),
    [automationName, setAutomationName] = useState("Ежемесячное письмо на пропуска"),
    [automationDay, setAutomationDay] = useState("20"),
    [automationRecipient, setAutomationRecipient] = useState(""),
    [automationSubject, setAutomationSubject] = useState("Заявка на пропуска на {next_month}"),
    [automationBody, setAutomationBody] = useState("Просьба оформить пропуска на {next_month} по проекту «{project}», договор {contract}."),
    [automationTaskTitle, setAutomationTaskTitle] = useState("Проверить и отправить письмо на пропуска на {next_month}"),
    [automationContractId, setAutomationContractId] = useState(0),
    [automationDocumentId, setAutomationDocumentId] = useState(0),
    [contracts, setContracts] = useState<ContractRow[]>([]),
    [documentRows, setDocumentRows] = useState<DocumentRow[]>([]),
    [selectedDocument, setSelectedDocument] = useState<DocumentDetail | null>(
      null,
    );
  const [projects, setProjects] = useState<Project[]>([]),
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
  const [analytics, setAnalytics] = useState<ProjectAnalytics | null>(null);
  const [integrationItems, setIntegrationItems] = useState<IntegrationItem[]>([]);
  const [sourceFolderId, setSourceFolderId] = useState("root");
  const [sourceBreadcrumbs, setSourceBreadcrumbs] = useState<DriveBreadcrumb[]>([
    { id: "root", name: "Мой диск" },
  ]);
  const {
    finance, financeCandidates, financeStructuredPreview, financeStructuredRows,
    selectedFinanceContractId, financeKind, financeTitle, financeAmount, financeDate,
    financeExtra, financeSourceDocumentId, financeScheduleItemId, financeBudgetLineId,
    setFinanceStructuredPreview, setFinanceStructuredRows, setSelectedFinanceContractId,
    setFinanceKind, setFinanceTitle, setFinanceAmount, setFinanceDate, setFinanceExtra,
    setFinanceSourceDocumentId, setFinanceScheduleItemId, setFinanceBudgetLineId,
    loadFinance, prepareFinanceItem, useFinanceCandidate, importStructuredFinance,
    addFinanceItem, confirmFinance, confirmCashPayment, updateScheduleActual, recordFinanceActual,
  } = useFinanceController({ ready, projectId, setNotice, setError });
  const loadSequenceRef = useRef(0);

  async function activateProject(id: number) {
    rememberProject(id);
    setActive("Рабочий центр");
    await load(id);
  }

  async function load(preferredProjectId?: number) {
    const loadSequence = ++loadSequenceRef.current;
    try {
      setError("");
      const p = await api("/projects/");
      setProjects(p.projects);
      const requestedProjectId = preferredProjectId ?? projectIdRef.current;
      const id = p.projects.some((item: Project) => item.id === requestedProjectId)
        ? requestedProjectId
        : p.projects[0]?.id || 0;
      if (id) {
        if (loadSequence !== loadSequenceRef.current) return;
        rememberProject(id);
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
          analyticsData,
          integrationData,
          automationData,
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
          api(`/analytics/project?project_id=${id}`).catch(() => null),
          api(`/integrations/project?project_id=${id}`).catch(() => ({ adapters: [] })),
          api(`/ai-secretary/automations?project_id=${id}`).catch(() => ({ rules: [] })),
        ]);
        if (
          loadSequence !== loadSequenceRef.current ||
          projectIdRef.current !== id
        ) return;
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
        setAnalytics(analyticsData);
        setIntegrationItems(integrationData.adapters);
        setAutomationRules(automationData.rules);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function loadIntegrations() {
    if (!projectId) return;
    try {
      setError("");
      const [google, health, catalog] = await Promise.all([
        api(`/projects/${projectId}/google/status`),
        api("/api/readiness"),
        api(`/integrations/project?project_id=${projectId}`),
      ]);
      setGoogleState(google);
      setSystemState(health);
      setIntegrationItems(catalog.adapters);
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
      setNotice(`Проект «${created.name}» создан`);
      await activateProject(created.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function archiveProject(project: Project) {
    if (projects.length <= 1) {
      setError("Нельзя архивировать единственный рабочий проект. Сначала создайте другой проект.");
      return;
    }
    if (!window.confirm(`Архивировать проект «${project.name}»? Данные сохранятся, безопасные копии на Drive останутся.`)) return;
    try {
      setError("");
      await api(`/projects/${project.id}`, { method: "DELETE" });
      const remaining = projects.filter((item) => item.id !== project.id);
      setProjects(remaining);
      if (project.id === projectId) await activateProject(remaining[0].id);
      setNotice(`Проект «${project.name}» перемещён в архив`);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function cleanupProjectCopies(project: Project) {
    try {
      setError("");
      const summary = await api(`/projects/${project.id}/safe-copies`);
      if (!summary.count) {
        setNotice("Безопасных копий PU Workspace для очистки нет");
        return;
      }
      const confirmation = window.prompt(
        `Будут перемещены в корзину Google Drive только ${summary.count} безопасных копий PU Workspace. Оригиналы не затрагиваются. Для подтверждения введите точное название проекта:`,
      );
      if (confirmation === null) return;
      const result = await api(`/projects/${project.id}/safe-copies/trash`, {
        method: "POST",
        body: JSON.stringify({ confirmation }),
      });
      setNotice(`В корзину Google Drive перемещено безопасных копий: ${result.trashed}. Оригиналы не изменены.`);
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
      const created = await api(`/projects/${projectId}/contracts`, {
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
      setSelectedFinanceContractId(created.id);
      setNotice("Договор добавлен; черновик ГПР создан, контур ДДС готов к заполнению");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function linkContractDocument(contractId: number, documentId: number) {
    try {
      await api(`/projects/${projectId}/contracts/${contractId}`, {
        method: "PATCH",
        body: JSON.stringify({ source_document_id: documentId || null }),
      });
      setNotice(documentId ? "Документ-источник привязан к договору" : "Связь с документом снята");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function suggestContractDocuments(contractId: number) {
    try {
      setError("");
      setContractCandidateBusy(contractId);
      const result = await api(`/projects/${projectId}/contracts/${contractId}/source-candidates`);
      setContractSourceCandidates((current) => ({ ...current, [contractId]: result.candidates || [] }));
      setContractDocumentTabs((current) => ({ ...current, [contractId]: "recommended" }));
      setNotice(result.recommended_document_id
        ? "Вероятный документ договора найден. Проверьте объяснение и подтвердите привязку."
        : "Точного совпадения не найдено. Используйте поиск или сначала завершите анализ документов.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setContractCandidateBusy(0);
    }
  }
  async function analyzeContract(contractId: number) {
    try {
      setError("");
      const result = await api(`/projects/${projectId}/contracts/${contractId}/analyze`, {
        method: "POST",
      });
      setNotice(
        `Договор проанализирован: первичных задач ${result.analysis.tasks}, обязательств ${result.analysis.obligations}, рисков ${result.analysis.risks}, решений ${result.analysis.decisions}. Теперь привяжите ГПР, бюджет и ДДС.`,
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function openContractControl(contractId: number) {
    try {
      setError("");
      const result = await api(`/projects/${projectId}/contracts/${contractId}/initialize-control`, {
        method: "POST",
      });
      setSelectedFinanceContractId(contractId);
      setActive("Исполнение и финансы");
      setNotice(result.created
        ? "Цепочка договора создана: заполните этапы ГПР, затем бюджет и ДДС"
        : "Цепочка договора открыта: ГПР, бюджет и ДДС связаны с договором");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function openSources(folderId = "root") {
    try {
      setError("");
      const targetProjectId = projectIdRef.current;
      const d = await api(
        `/projects/${targetProjectId}/source-folders/discover?folder_id=${encodeURIComponent(folderId)}`,
      );
      setFolders(d.folders);
      setSourceFolderId(d.folder_id || folderId);
      setSourceBreadcrumbs(
        d.breadcrumbs || [{ id: "root", name: "Мой диск" }],
      );
      setShowSources(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function queueFolder(folder: DriveFolder) {
    try {
      const targetProjectId = projectIdRef.current;
      setBusyFolder(folder.id);
      setError("");
      const queued = await api(
        `/projects/${targetProjectId}/source-folders/${folder.id}/snapshot-queue`,
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
      setNotice(
        `Папка «${folder.name}» подключена. Создаётся безопасная копия, выполняются анализ и стандартизация имён. Оригиналы не изменяются.`,
      );
      await load(targetProjectId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyFolder("");
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
        `/projects/${projectId}/snapshots/${folder.snapshot_id}/standardize`,
        { method: "POST" },
      );
      if (result.already_analyzed) {
        setFolders((items) =>
          items.map((item) =>
            item.id === folder.id ? { ...item, analyzed: true } : item,
          ),
        );
        setNotice(`${folder.name} уже поставлена на безопасную стандартизацию`);
      } else {
        setFolders((items) =>
          items.map((item) =>
            item.id === folder.id
              ? { ...item, analysis_status: "analyzing" }
              : item,
          ),
        );
        setNotice(
          `Создание безопасной копии, анализ и стандартизация «${folder.name}» запущены. Страницу можно закрыть.`,
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
  function startTaskCompletion(task: TaskRow) {
    setCompletionTaskId(task.id);
    setCompletionNote(task.result_note || "");
    setCompletionDocumentId(task.completion_document_id || 0);
  }
  async function loadTaskHistory(task: TaskRow) {
    if (taskHistoryId === task.id) {
      setTaskHistoryId(0);
      setTaskHistory([]);
      return;
    }
    try {
      const result = await api(`/tasks/${task.id}/history`);
      setTaskHistory(result.history);
      setTaskHistoryId(task.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function updateTask(task: TaskRow, status: string) {
    try {
      setError("");
      const result_note = status === "completed" ? completionNote.trim() : undefined;
      if (status === "completed" && !result_note) {
        setError("Кратко укажите, что выполнено. Подтверждающий документ добавляется по желанию.");
        return;
      }
      await api(`/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status,
          result_note,
          ...(status === "completed" ? { completion_document_id: completionDocumentId || null } : {}),
        }),
      });
      setNotice(
        status === "completed"
          ? "Задача завершена и синхронизирована"
          : "Задача взята в работу",
      );
      setCompletionTaskId(0);
      setCompletionNote("");
      setCompletionDocumentId(0);
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
  async function createAutomationRule() {
    const day = Number(automationDay);
    if (!automationContractId) {
      setError("Сначала выберите договор для регламента");
      return;
    }
    if (!automationName.trim()) {
      setError("Укажите название ежемесячного регламента");
      return;
    }
    if (!Number.isInteger(day) || day < 1 || day > 31) {
      setError("Укажите число месяца от 1 до 31");
      return;
    }
    if (!automationRecipient.trim() || !automationRecipient.includes("@")) {
      setError("Укажите email получателя письма, например office@example.com");
      return;
    }
    if (!automationSubject.trim() || !automationBody.trim() || !automationTaskTitle.trim()) {
      setError("Заполните тему, текст письма и название контрольной задачи");
      return;
    }
    try {
      setError("");
      await api("/ai-secretary/automations", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          contract_id: automationContractId || null,
          source_document_id: automationDocumentId || null,
          name: automationName.trim(),
          day_of_month: day,
          recipient_to: automationRecipient.trim(),
          subject_template: automationSubject.trim(),
          body_template: automationBody.trim(),
          task_title_template: automationTaskTitle.trim(),
        }),
      });
      setNotice("Регламент создан: в установленный день появятся задача и черновик письма; отправка только после подтверждения");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function openAutomationContractPicker() {
    const select = document.getElementById("automation-contract-select") as
      | (HTMLSelectElement & { showPicker?: () => void })
      | null;
    select?.focus();
    select?.showPicker?.();
  }
  async function setAutomationActive(rule: AutomationRule, activeValue: boolean) {
    try {
      await api(`/ai-secretary/automations/${rule.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: activeValue }),
      });
      setNotice(activeValue ? "Регламент включён" : "Регламент приостановлен");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function runAutomationNow(rule: AutomationRule) {
    try {
      await api(`/ai-secretary/automations/${rule.id}/run-now`, { method: "POST" });
      setNotice("Созданы задача и черновик письма. Проверьте их перед подтверждением и отправкой");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function confirmMessageContext(message: InboxMessage) {
    try {
      await api(`/ai-secretary/inbox/${message.id}/confirm-context`, {
        method: "POST",
        body: JSON.stringify({
          project_id: message.project_id || projectId,
          contract_id: message.contract_id || null,
        }),
      });
      setNotice("Связь сообщения с проектом и договором подтверждена");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function confirmMessageContextBulk() {
    const targetProjectId = bulkInboxProjectId || projectId;
    if (!selectedInboxIds.length || !targetProjectId) return;
    if (!window.confirm(`Перенести и подтвердить ${selectedInboxIds.length} писем в выбранном проекте?`)) return;
    try {
      const result = await api("/ai-secretary/inbox/confirm-context-bulk", {
        method: "POST",
        body: JSON.stringify({
          message_ids: selectedInboxIds,
          project_id: targetProjectId,
          contract_id: bulkInboxContractId || null,
        }),
      });
      setSelectedInboxIds([]);
      setNotice(`Подтверждено писем: ${result.confirmed}; перенесено между проектами: ${result.moved}`);
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
  async function importInboxAttachment(message: InboxMessage, index: number) {
    const attachment = message.attachments[index];
    if (!window.confirm(`Импортировать «${attachment.name}» в документы проекта и выполнить анализ?`)) return;
    try {
      const result = await api(`/ai-secretary/inbox/${message.id}/attachments/${index}/import`, { method: "POST" });
      setNotice(result.already_indexed
        ? `Вложение «${result.name}» уже находится в документах проекта.`
        : `Вложение «${result.name}» добавлено: задач ${result.tasks}, рисков ${result.risks}, черновиков ${result.drafts}.`);
      await load();
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
          publish_task: true,
          publish_calendar: Boolean(task.due_date),
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
  async function assignTask(task: TaskRow, assigneeUserId: number) {
    try {
      const assignee = members.find((member) => member.user_id === assigneeUserId);
      await api(`/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ assignee_user_id: assigneeUserId }),
      });
      setTasks((rows) => rows.map((row) => row.id === task.id
        ? { ...row, assignee_user_id: assigneeUserId, assignee_name: assignee?.name || row.assignee_name }
        : row));
      setNotice(`Исполнитель задачи: ${assignee?.name || "участник проекта"}`);
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
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    const handleInstall = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as InstallPromptEvent);
    };
    const handleInstalled = () => setInstallPrompt(null);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    window.addEventListener("beforeinstallprompt", handleInstall);
    window.addEventListener("appinstalled", handleInstalled);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("beforeinstallprompt", handleInstall);
      window.removeEventListener("appinstalled", handleInstalled);
    };
  }, []);
  async function installApp() {
    if (!installPrompt) return;
    await installPrompt.prompt();
    await installPrompt.userChoice;
    setInstallPrompt(null);
  }
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
    if (!ready || !projectId || !processingQueue?.summary.active) return;
    const timer = window.setInterval(async () => {
      try {
        const queue = await api(`/projects/${projectId}/processing-queue`);
        setProcessingQueue(queue);
        if (!queue.summary.active) await load();
      } catch {
        // A transient refresh failure must not interrupt the active screen.
      }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [ready, projectId, processingQueue?.summary.active]);
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
    if ((active === "Документы" || active === "Центр знаний") && documentRows.length && !selectedDocument)
      openDocument(documentRows[0]);
  }, [active, documentRows.length, projectId]);
  useEffect(() => {
    if (!ready || !projectId || active !== "Центр знаний") return;
    const timer = window.setTimeout(async () => {
      try {
        const suffix = query.trim()
          ? `?search=${encodeURIComponent(query.trim())}&limit=200`
          : "?limit=200";
        const result = await api(`/projects/${projectId}/documents${suffix}`);
        setDocumentRows(result.documents);
        if (result.documents.length) await openDocument(result.documents[0]);
        else setSelectedDocument(null);
      } catch (e) {
        setError((e as Error).message);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [ready, projectId, active, query]);
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
  function openMetric(label: string) {
    if (label === "Обязательства") {
      setActive("Обязательства");
      return;
    }
    if (label === "Просрочено") {
      setTaskFilter("overdue");
      setActive("Задачи");
      return;
    }
    if (label === "Риски" || label === "Ждут решения" || label === "Требуют внимания") {
      setActive("Риски и решения");
      return;
    }
    if (label === "Уведомления") setActive("Уведомления");
  }
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
  const analyticsLabel = (value: string) => ({
    assigned: "Назначены",
    in_progress: "В работе",
    completed: "Выполнены",
    needs_confirmation: "Требуют подтверждения",
    confirmed: "Подтверждены",
    mitigating: "Снижаются",
    resolved: "Закрыты",
    low: "Низкая",
    medium: "Средняя",
    high: "Высокая",
    critical: "Критическая",
    email: "Email",
    telegram: "Telegram",
    manual: "Вручную",
    document: "Документ",
    discovered: "Обнаружены",
    indexed: "Проиндексированы",
    unknown: "Не определено",
  } as Record<string, string>)[value] || value.replaceAll("_", " ");
  const financeContract = contracts.find((contract) => contract.id === selectedFinanceContractId);
  const financeBaselines = finance?.baselines.filter((row) => row.contract_id === selectedFinanceContractId) || [];
  const financeBaselineIds = new Set(financeBaselines.map((row) => row.id));
  const financeSchedule = finance?.schedule.filter((row) => financeBaselineIds.has(row.baseline_id)) || [];
  const financeBudget = finance?.budget.filter((row) => row.contract_id === selectedFinanceContractId) || [];
  const financeCash = finance?.cash_flow.filter((row) => row.contract_id === selectedFinanceContractId) || [];
  const financeActs = finance?.acts.filter((row) => row.contract_id === selectedFinanceContractId) || [];
  const visibleDocuments = documentRows.filter(
    (item) =>
      active === "Центр знаний" ||
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
            {processingQueue &&
              (processingQueue.summary.active > 0 ||
                processingQueue.summary.failed > 0 ||
                processingQueue.summary.dead_letter > 0) && (
                <button
                  className={`queue-status ${processingQueue.summary.failed || processingQueue.summary.dead_letter ? "has-errors" : ""}`}
                  onClick={() => setActive("Настройки")}
                  title="Открыть очередь обработки"
                >
                  <Activity />
                  <span>
                    {processingQueue.summary.active
                      ? `В работе: ${processingQueue.summary.active}`
                      : `Ошибок: ${processingQueue.summary.failed + processingQueue.summary.dead_letter}`}
                  </span>
                </button>
              )}
            <span className={`connection-status ${online ? "online" : "offline"}`}>
              {online ? "Онлайн" : "Офлайн"}
            </span>
            {installPrompt && (
              <button className="install-app" onClick={installApp} title="Установить PU Workspace">
                <Download />
                <span>Установить</span>
              </button>
            )}
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
              onChange={(e) => {
                const id = Number(e.target.value);
                rememberProject(id);
                void load(id);
              }}
            >
              {projects.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button className="icon" onClick={() => load()}>
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
                      <label className="task-assignee">
                        <span>Исполнитель</span>
                        <select
                          aria-label={`Исполнитель задачи ${task.title}`}
                          value={task.assignee_user_id}
                          onChange={(event) => assignTask(task, Number(event.target.value))}
                        >
                          {members.map((member) => (
                            <option value={member.user_id} key={member.user_id}>
                              {member.name} · {member.role}
                            </option>
                          ))}
                        </select>
                      </label>
                      {task.external_action_status !== "executed" && (
                        <button onClick={() => approveExternal(task)}>
                          Поставить задачу
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
                          onClick={() => startTaskCompletion(task)}
                        >
                          Завершить
                        </button>
                      )}
                      <button className="secondary" onClick={() => loadTaskHistory(task)}>
                        История
                      </button>
                    </div>
                    {completionTaskId === task.id && (
                      <div className="task-completion">
                        <strong>Подтверждение выполнения</strong>
                        <textarea
                          value={completionNote}
                          onChange={(event) => setCompletionNote(event.target.value)}
                          placeholder="Что выполнено и какой результат получен *"
                        />
                        <select
                          value={completionDocumentId}
                          onChange={(event) => setCompletionDocumentId(Number(event.target.value))}
                        >
                          <option value={0}>Без вложения — это допустимо</option>
                          {documentRows.map((document) => (
                            <option value={document.id} key={document.id}>{document.name}</option>
                          ))}
                        </select>
                        <small>Необязательно: выберите акт, письмо, счёт, фото или другой документ проекта.</small>
                        <div className="task-completion-actions">
                          <button className="secondary" onClick={() => setCompletionTaskId(0)}>Отмена</button>
                          <button className="complete" onClick={() => updateTask(task, "completed")}>Подтвердить завершение</button>
                        </div>
                      </div>
                    )}
                    {taskHistoryId === task.id && (
                      <div className="task-history">
                        <strong>История задачи и решений</strong>
                        {taskHistory.map((item, index) => (
                          <div className="task-history-row" key={`${item.changed_at}-${index}`}>
                            <time>{new Date(item.changed_at).toLocaleString("ru-RU")}</time>
                            <span>{item.changed_by}</span>
                            <b>{item.action === "created" ? "Создана" : item.action === "completed" ? "Завершена" : "Изменена"}</b>
                            {item.old_status !== item.new_status && <small>{item.old_status || "—"} → {item.new_status || "—"}</small>}
                            {item.result_note && <p>{item.result_note}</p>}
                            {item.completion_document_name && <p>Подтверждение: {item.completion_document_name}</p>}
                            {item.details && <p>{item.details}</p>}
                          </div>
                        ))}
                      </div>
                    )}
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
                  <button
                    type="button"
                    className={String(tone)}
                    key={String(label)}
                    onClick={() => openMetric(String(label))}
                    aria-label={`Открыть раздел: ${label}`}
                  >
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </button>
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
                    <button onClick={() => setActive("Риски и решения")}>Открыть реестр</button>
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
                      <button onClick={() => void openSources("root")}>
                        Все источники
                      </button>
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
                {!latestSnapshot && googleState?.authorized && (
                  <section className="card span-3 source-card source-onboarding">
                    <div className="source-icon">
                      <FolderTree />
                    </div>
                    <div>
                      <span className="eyebrow">РАБОЧИЙ ИСТОЧНИК</span>
                      <h2>Google Drive подключён</h2>
                      <p>
                        Выберите папку, которая станет рабочим источником этого
                        проекта. Оригиналы не изменяются.
                      </p>
                    </div>
                    <div className="source-actions">
                      <button onClick={() => void openSources("root")}>
                        Выбрать рабочую папку
                      </button>
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
                        {sourceFolderId === "root" && (
                          <button
                            className="queue-all"
                            disabled={busyAll}
                            onClick={queueAllFolders}
                          >
                            {busyAll ? "Добавление…" : "Подготовить все папки"}
                          </button>
                        )}
                        <button onClick={() => setShowSources(false)}>
                          Закрыть
                        </button>
                      </div>
                    </div>
                    <div className="source-breadcrumbs">
                      {sourceBreadcrumbs.map((crumb, index) => (
                        <button
                          key={crumb.id}
                          disabled={index === sourceBreadcrumbs.length - 1}
                          onClick={() => void openSources(crumb.id)}
                        >
                          {crumb.name}
                        </button>
                      ))}
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
                                  ? `Создаётся безопасная копия, анализируются документы и имена · ${(folder.item_count || 0).toLocaleString("ru-RU")} объектов`
                                  : folder.analysis_status === "failed"
                                    ? `Ошибка анализа: ${folder.analysis_error || "можно повторить"}`
                                    : folder.analysis_status === "ready"
                                      ? folder.analysis_result?.mode === "safe_copy"
                                        ? `Безопасная копия готова: ${folder.analysis_result?.copy_folder_name || "имена стандартизированы"}. Оригиналы не изменены.`
                                        : `Готово: документов ${folder.analysis_result?.documents || 0}, задач ${folder.analysis_result?.tasks || 0}, рисков ${folder.analysis_result?.risks || 0}, черновиков ${folder.analysis_result?.drafts || 0}`
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
                            <button onClick={() => void openSources(folder.id)}>
                              Открыть
                            </button>
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
                                  folder.analysis_status === "analyzing" ||
                                  folder.analysis_result?.mode === "safe_copy"
                                }
                                onClick={() => analyzeFolder(folder)}
                              >
                                {busyFolder === folder.id ||
                                folder.analysis_status === "analyzing"
                                  ? "Анализ…"
                                  : folder.analysis_result?.mode === "safe_copy"
                                    ? "Стандартизирована"
                                  : folder.analyzed
                                    ? "Проанализирована"
                                    : folder.analysis_status === "failed"
                                      ? "Повторить стандартизацию"
                                      : "Создать копию и стандартизировать"}
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
                                    : folder.registered
                                      ? "Обновить копию и анализ"
                                      : "Подключить и стандартизировать"}
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
      {active === "Аналитика" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page analytics-page">
            <section className="analytics-hero card">
              <div>
                <span className="eyebrow">PROJECT CORE</span>
                <h2>Состояние проекта</h2>
                <p>Единая аналитика по документам, задачам, рискам и входящим — независимо от подключённых сервисов.</p>
              </div>
              <button onClick={() => load()}><RefreshCw /> Обновить</button>
            </section>
            {analytics ? (
              <>
                <section className="analytics-metrics">
                  {[
                    ["Документы", analytics.summary.documents],
                    ["Извлечена сводка", `${analytics.summary.document_coverage}%`],
                    ["Открытые задачи", analytics.summary.open_tasks],
                    ["Просрочено", analytics.summary.overdue_tasks],
                    ["Открытые риски", analytics.summary.open_risks],
                    ["Входящие без реакции", analytics.summary.pending_messages],
                  ].map(([label, value]) => <article key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}
                </section>
                <section className="analytics-grid">
                  {[
                    ["Источники документов", analytics.documents_by_source],
                    ["Состояние документов", analytics.documents_by_status],
                    ["Состояние задач", analytics.tasks_by_status],
                    ["Критичность рисков", analytics.risks_by_criticality],
                    ["Каналы входящих", analytics.messages_by_channel],
                  ].map(([title, rawRows]) => {
                    const rows = rawRows as AnalyticsDistribution;
                    const maximum = Math.max(1, ...rows.map((row) => row.count));
                    return <section className="card analytics-panel" key={String(title)}>
                      <h2>{title as string}</h2>
                      <div className="analytics-bars">
                        {rows.map((row) => <div key={row.key}>
                          <span>{analyticsLabel(row.key)}</span><b>{row.count}</b>
                          <i><em style={{ width: `${Math.max(4, row.count / maximum * 100)}%` }} /></i>
                        </div>)}
                        {!rows.length && <p className="analytics-empty">Данных пока нет</p>}
                      </div>
                    </section>;
                  })}
                  <section className="card analytics-panel analytics-summary">
                    <h2>Контур управления</h2>
                    <p><strong>{analytics.summary.active_contracts}</strong> активных договоров из {analytics.summary.contracts}</p>
                    <p><strong>{analytics.summary.pending_decisions}</strong> решений требуют фиксации</p>
                    <p><strong>{analytics.summary.messages}</strong> входящих обработано системой</p>
                  </section>
                </section>
              </>
            ) : <section className="card empty"><Activity /><p>Аналитика загружается…</p></section>}
          </div>
        </section>
      )}
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
            <section className="card finance-contract-chain">
              <div>
                <span className="eyebrow">ЛОГИЧЕСКАЯ ЦЕПОЧКА</span>
                <h2>Договор → ГПР → бюджет → ДДС → акты</h2>
                <p>Все новые записи ниже автоматически получают связь с выбранным договором.</p>
              </div>
              <select value={selectedFinanceContractId} onChange={(e) => setSelectedFinanceContractId(Number(e.target.value))}>
                <option value={0}>Весь проект / без договора</option>
                {contracts.map((contract) => <option value={contract.id} key={contract.id}>{contract.number} — {contract.title}</option>)}
              </select>
            </section>
            <section className="card finance-chain-guide">
              <div className="card-head">
                <div>
                  <h2>Мастер запуска исполнения</h2>
                  <p>{financeContract
                    ? `Договор ${financeContract.number}: выполните шаги слева направо.`
                    : "Выберите договор выше — записи не должны терять договорный контекст."}</p>
                </div>
              </div>
              <div className="finance-chain-steps">
                {[
                  ["1", "Договор", Boolean(financeContract), "contracts"],
                  ["2", "ГПР", financeSchedule.length > 0, "schedule"],
                  ["3", "Бюджет", financeBudget.length > 0, "budget"],
                  ["4", "ДДС / счёт", financeCash.length > 0, "invoice"],
                  ["5", "Акты", financeActs.length > 0, "act"],
                ].map(([number, label, complete, kind]) => (
                  <button
                    type="button"
                    className={complete ? "complete" : "pending"}
                    disabled={!financeContract || kind === "contracts"}
                    onClick={() => kind !== "contracts" && prepareFinanceItem(String(kind))}
                    key={String(label)}
                  >
                    <b>{complete ? "✓" : number}</b>
                    <span>{label}</span>
                    <small>{complete ? "готово" : kind === "contracts" ? "выберите договор" : "добавить"}</small>
                  </button>
                ))}
              </div>
              {financeContract && !financeSchedule.length && (
                <p className="finance-next-action">Следующий шаг: добавьте этапы ГПР с плановыми сроками. После этого создайте бюджет и связывайте счета с этапом и строкой бюджета.</p>
              )}
            </section>
            <section className="card finance-document-assistant">
              <div className="card-head">
                <div>
                  <span className="eyebrow">АНАЛИЗ ПРОЕКТНОЙ ПАПКИ</span>
                  <h2>Найденные ГПР, бюджеты, ДДС, счета и акты</h2>
                  <p>Система предлагает роль документа по названию и извлечённому тексту. Оригинал не меняется; перед созданием записи проверьте поля.</p>
                </div>
                <button type="button" onClick={loadFinance}>Обновить анализ</button>
              </div>
              <div className="finance-candidates">
                {financeCandidates.slice(0, 12).map((candidate) => (
                  <article className={candidate.already_linked ? "linked" : ""} key={candidate.document_id}>
                    <div>
                      <span>{({ schedule: "ГПР", budget: "Бюджет / смета", invoice: "Счёт", "cash-flow": "ДДС", act: "Акт" } as Record<string, string>)[candidate.kind]}</span>
                      <b>{candidate.score}%</b>
                    </div>
                    <strong title={candidate.name}>{candidate.name}</strong>
                    <small>{candidate.reasons.join("; ") || "совпадение по структуре документа"}</small>
                    <small>{[
                      candidate.hints.amount ? money(Number(candidate.hints.amount)) : "",
                      candidate.hints.date || "",
                      candidate.hints.number ? `№ ${candidate.hints.number}` : "",
                    ].filter(Boolean).join(" · ")}</small>
                    <button type="button" disabled={candidate.already_linked} onClick={() => useFinanceCandidate(candidate)}>
                      {candidate.already_linked ? "Уже привязан" : "Проверить и использовать"}
                    </button>
                  </article>
                ))}
                {!financeCandidates.length && (
                  <p className="finance-empty">Подходящие документы пока не распознаны. Завершите анализ подключённой рабочей копии и обновите этот блок.</p>
                )}
              </div>
            </section>
            {financeStructuredPreview && <section className="card structured-import" id="structured-import">
              <div className="card-head">
                <div>
                  <span className="eyebrow">ПАКЕТНОЕ ПРЕДЛОЖЕНИЕ</span>
                  <h2>{financeStructuredPreview.name}</h2>
                  <p>Сопоставлено колонок: {Object.keys(financeStructuredPreview.mapping).length}. Выберите строки; импорт создаст предложения со ссылкой на строку источника.</p>
                </div>
                <button className="secondary" onClick={() => { setFinanceStructuredPreview(null); setFinanceStructuredRows([]); }}>Закрыть</button>
              </div>
              {financeStructuredPreview.issues.map((issue) => <p className="finance-warning" key={issue}>{issue}</p>)}
              <div className="structured-table">
                <table>
                  <thead><tr><th></th><th>Строка</th><th>Наименование</th><th>Дата / срок</th><th>Сумма / прогресс</th><th>Проверка</th></tr></thead>
                  <tbody>{financeStructuredPreview.rows.slice(0, 100).map((row) => <tr className={row.importable ? "" : "invalid"} key={row.source_row}>
                    <td><input type="checkbox" disabled={!row.importable} checked={financeStructuredRows.includes(row.source_row)} onChange={(e) => setFinanceStructuredRows((selected) => e.target.checked ? [...selected, row.source_row] : selected.filter((value) => value !== row.source_row))} /></td>
                    <td>{row.source_row}</td>
                    <td>{row.title || "—"}<small>{row.category}</small></td>
                    <td>{row.planned_date || row.planned_finish || row.planned_start || "—"}</td>
                    <td>{row.amount ? money(Number(row.amount)) : `${row.progress || 0}%`}</td>
                    <td>{row.issues.length ? row.issues.join("; ") : "готово к предложению"}</td>
                  </tr>)}</tbody>
                </table>
              </div>
              {financeStructuredPreview.truncated && <p className="finance-warning">Показаны первые 500 строк. Разделите файл или импортируйте его частями.</p>}
              <div className="structured-actions">
                <span>Выбрано строк: <strong>{financeStructuredRows.length}</strong></span>
                <button disabled={!financeStructuredRows.length} onClick={importStructuredFinance}>Создать пакет предложений</button>
              </div>
            </section>}
            <section className="card finance-entry" id="finance-entry">
              <div>
                <h2>Добавить управленческую запись</h2>
                <p>
                  Новая запись создаётся как предложение и не влияет на
                  подтверждённый прогноз.
                </p>
                {financeSourceDocumentId > 0 && <p className="finance-source-note">Источник: документ #{financeSourceDocumentId}. Связь сохранится для счёта или акта.</p>}
              </div>
              <div>
                <select
                  value={financeKind}
                  onChange={(e) => setFinanceKind(e.target.value)}
                >
                  <option value="budget">Строка бюджета</option>
                  <option value="cash-in">Поступление ДДС</option>
                  <option value="cash-out">Выплата ДДС</option>
                  <option value="invoice">Счёт → предложение ДДС</option>
                  <option value="procurement">Закупка / поставка</option>
                  <option value="act">Акт</option>
                  <option value="baseline">Версия ГПР</option>
                  <option value="schedule">Этап ГПР</option>
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
                {["cash-in", "cash-out", "invoice", "procurement", "act"].includes(
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
                          : financeKind === "schedule"
                            ? "Комментарий к этапу"
                          : "Контрагент / поставщик"
                  }
                />
                {financeKind === "invoice" && (
                  <>
                    <select value={financeScheduleItemId} onChange={(e) => setFinanceScheduleItemId(Number(e.target.value))}>
                      <option value={0}>Связать с этапом ГПР (не выбран)</option>
                      {finance?.schedule.filter((stage) => {
                        const baseline = finance.baselines.find((row) => row.id === stage.baseline_id);
                        return !selectedFinanceContractId || baseline?.contract_id === selectedFinanceContractId;
                      }).map((stage) => <option key={stage.id} value={stage.id}>{stage.title}</option>)}
                    </select>
                    <select value={financeBudgetLineId} onChange={(e) => setFinanceBudgetLineId(Number(e.target.value))}>
                      <option value={0}>Связать со строкой бюджета (не выбрана)</option>
                      {finance?.budget.filter((row) => !selectedFinanceContractId || row.contract_id === selectedFinanceContractId)
                        .map((row) => <option key={row.id} value={row.id}>{row.description}</option>)}
                    </select>
                  </>
                )}
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
                  {finance?.baselines.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).map((item) => (
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
                  {!finance?.baselines.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).length && (
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
                  {finance?.budget.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).map((item) => (
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
                  {!finance?.budget.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).length && (
                    <p className="finance-empty">Строк бюджета пока нет.</p>
                  )}
                </div>
              </article>
              <article className="card">
                <h2>ДДС</h2>
                <div className="finance-list">
                  {finance?.cash_flow.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).map((item) => (
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
                      {item.status === "approved" && (
                        <button onClick={() => confirmCashPayment(item.id, Number(item.planned_amount))}>
                          Подтвердить оплату
                        </button>
                      )}
                    </div>
                  ))}
                  {!finance?.cash_flow.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).length && (
                    <p className="finance-empty">План ДДС пока пуст.</p>
                  )}
                </div>
              </article>
              <article className="card">
                <h2>Закупки и поставки</h2>
                <div className="finance-list">
                  {finance?.procurement.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).map((item) => (
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
                  {!finance?.procurement.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).length && (
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
                  {finance?.acts.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).map((item) => (
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
                  {!finance?.acts.filter((item) => !selectedFinanceContractId || item.contract_id === selectedFinanceContractId).length && (
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
                      <label>1. Документ-источник</label>
                      <button
                        type="button"
                        className="secondary"
                        disabled={contractCandidateBusy === item.id}
                        onClick={() => suggestContractDocuments(item.id)}
                      >
                        {contractCandidateBusy === item.id
                          ? "Анализирую реестр…"
                          : "Найти договор по номеру, контрагенту и тексту"}
                      </button>
                      <div className="contract-source-tabs">
                        {([
                          ["recommended", "Рекомендованные"],
                          ["server", "Сервер / реестр"],
                          ["upload", "Облако / загрузки"],
                          ["google", "Google Drive"],
                        ] as const).map(([source, title]) => (
                          <button
                            type="button"
                            className={(contractDocumentTabs[item.id] || "recommended") === source ? "selected" : "secondary"}
                            onClick={() => setContractDocumentTabs((current) => ({ ...current, [item.id]: source }))}
                            key={source}
                          >
                            {title}
                          </button>
                        ))}
                      </div>
                      <input
                        value={contractDocumentQueries[item.id] || ""}
                        onChange={(event) => setContractDocumentQueries((current) => ({ ...current, [item.id]: event.target.value }))}
                        placeholder="Поиск документа по названию"
                      />
                      <select
                        value={item.source_document_id || 0}
                        onChange={(e) => linkContractDocument(item.id, Number(e.target.value))}
                      >
                        <option value={0}>Выберите документ договора</option>
                        {documentRows
                          .filter((document) => {
                            const tab = contractDocumentTabs[item.id] || "recommended";
                            const source = (document.source || "").toLowerCase();
                            const candidate = (contractSourceCandidates[item.id] || []).find((row) => row.document_id === document.id);
                            const sourceMatches = tab === "recommended"
                              ? Boolean(candidate && candidate.score > 0)
                              : tab === "server"
                              ? true
                              : tab === "upload"
                                ? !source.includes("google")
                                : source.includes("google");
                            const search = (contractDocumentQueries[item.id] || "").trim().toLocaleLowerCase("ru-RU");
                            return sourceMatches && (!search || document.name.toLocaleLowerCase("ru-RU").includes(search));
                          })
                          .sort((left, right) => {
                            const scores = contractSourceCandidates[item.id] || [];
                            const leftScore = scores.find((row) => row.document_id === left.id)?.score || 0;
                            const rightScore = scores.find((row) => row.document_id === right.id)?.score || 0;
                            return rightScore - leftScore || right.id - left.id;
                          })
                          .map((document) => (
                            <option value={document.id} key={document.id}>
                              {(() => {
                                const score = (contractSourceCandidates[item.id] || []).find((row) => row.document_id === document.id)?.score;
                                return `${score ? `${score}% · ` : ""}${document.name}`;
                              })()}
                            </option>
                          ))}
                      </select>
                      {(contractDocumentTabs[item.id] || "recommended") === "recommended" &&
                        !(contractSourceCandidates[item.id] || []).length && (
                          <small>Нажмите «Найти договор…»: система проверит не только имя файла, но и извлечённый текст.</small>
                        )}
                      {(contractSourceCandidates[item.id] || []).slice(0, 3).map((candidate) => (
                        <div className="contract-candidate" key={candidate.document_id}>
                          <span>
                            <strong>{candidate.score}% · {candidate.name}</strong>
                            <small>{candidate.reasons.join("; ") || "слабое совпадение"}</small>
                          </span>
                          <button
                            type="button"
                            className="secondary"
                            disabled={item.source_document_id === candidate.document_id}
                            onClick={() => linkContractDocument(item.id, candidate.document_id)}
                          >
                            {item.source_document_id === candidate.document_id ? "Привязан" : "Привязать"}
                          </button>
                        </div>
                      ))}
                      <small>
                        «Рекомендованные» ранжируются по реквизитам и тексту; «Сервер / реестр» показывает все документы проекта; «Облако / загрузки» — загруженные файлы;
                        «Google Drive» — документы, проиндексированные из подключённого Диска.
                      </small>
                      <button
                        className="secondary"
                        disabled={!item.source_document_id}
                        onClick={() => analyzeContract(item.id)}
                      >
                        2. Проанализировать договор и создать первичные задачи
                      </button>
                      {item.source_document_id && !item.analysis?.source_ready && (
                        <small>Сначала дождитесь завершения анализа рабочей папки: текст договора ещё не извлечён.</small>
                      )}
                      {item.analysis && (item.analysis.tasks > 0 || item.analysis.obligations > 0) && (
                        <div className="contract-analysis-status">
                          <span>Задачи <b>{item.analysis.tasks}</b></span>
                          <span>Обязательства <b>{item.analysis.obligations}</b></span>
                          <span>Риски <b>{item.analysis.risks}</b></span>
                          <span>Решения <b>{item.analysis.decisions}</b></span>
                        </div>
                      )}
                      <div className="contract-chain-status">
                        <span>3. ГПР <b>{finance?.baselines.filter((row) => row.contract_id === item.id).length ? "создан" : "не создан"}</b></span>
                        <span>4. Бюджет <b>{finance?.budget.filter((row) => row.contract_id === item.id).length || 0} строк</b></span>
                        <span>5. ДДС <b>{finance?.cash_flow.filter((row) => row.contract_id === item.id).length || 0} записей</b></span>
                      </div>
                      <button onClick={() => openContractControl(item.id)}>
                        {finance?.baselines.some((row) => row.contract_id === item.id)
                          ? "3. Открыть ГПР, бюджет и ДДС"
                          : "3. Создать ГПР, бюджет и ДДС"}
                      </button>
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
              {integrationItems.map((item) => (
                <article className="card integration-card" key={item.key}>
                  <div
                    className={`integration-icon ${item.connected ? "connected" : ""}`}
                  >
                    {item.capability === "channel" ? <Mail /> : item.capability === "ai" ? <Bot /> : item.capability === "storage" ? <FolderTree /> : <CalendarDays />}
                  </div>
                  <div>
                    <h2>{item.name}</h2>
                    <p>{item.description}</p>
                    <small>{item.capability} · {item.provider}</small>
                    {item.detail && <small className="integration-detail">{item.detail}</small>}
                  </div>
                  <span className={item.connected ? "connected" : ""}>
                    {item.connected ? "Готово" : item.available ? "Не подключено" : "Недоступно"}
                  </span>
                  {item.action === "sync" && item.connected ? (
                    <button onClick={() => syncGmail()} disabled={gmailSyncing}>
                      {gmailSyncing ? "Получаю…" : "Получить письма"}
                    </button>
                  ) : item.provider === "google_workspace" &&
                    item.capability === "storage" &&
                    item.connected ? (
                    <button
                      onClick={() => {
                        setActive("Рабочий центр");
                        void openSources("root");
                      }}
                    >
                      Выбрать папку
                    </button>
                  ) : item.action === "oauth" ? (
                    <button onClick={connectGoogle}>{item.connected ? "Переподключить" : "Подключить"}</button>
                  ) : item.action === "local_upload" ? (
                    <button onClick={() => { setActive("Рабочий центр"); setNotice("Нажмите «Загрузить рабочую папку» в рабочем центре"); }}>Загрузить папку</button>
                  ) : item.action === "ai_policy" ? (
                    <button onClick={() => setActive("Настройки")}>Политика AI</button>
                  ) : null}
                  {item.action === "sync" && gmailSyncStatus && (
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
              {!integrationItems.length && <div className="card empty"><Activity /><p>Каталог подключений загружается…</p></div>}
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
                <button onClick={() => load()}>Обновить</button>
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
            {active === "AI Secretary" && <section className="card automation-control">
              <div className="automation-heading">
                <div>
                  <span className="eyebrow">РЕГЛАМЕНТНЫЕ СЦЕНАРИИ</span>
                  <h2>AI Secretary — контролёр повторяющихся задач</h2>
                  <p>В нужный день создаются задача и черновик письма. Внешняя отправка всегда требует вашего подтверждения.</p>
                </div>
                <b>{automationRules.filter((rule) => rule.active).length} активных</b>
              </div>
              <div className="automation-form">
                <button className="secondary" onClick={openAutomationContractPicker}>1. Выбрать договор</button>
                <select
                  id="automation-contract-select"
                  value={automationContractId}
                  onChange={(e) => setAutomationContractId(Number(e.target.value))}
                >
                  <option value={0}>Выберите договор…</option>
                  {contracts.map((contract) => <option value={contract.id} key={contract.id}>{contract.number} — {contract.title}</option>)}
                </select>
                <input value={automationName} onChange={(e) => setAutomationName(e.target.value)} placeholder="Название регламента" />
                <label>Каждое число месяца<input type="number" min="1" max="31" value={automationDay} onChange={(e) => setAutomationDay(e.target.value)} /></label>
                <input type="email" value={automationRecipient} onChange={(e) => setAutomationRecipient(e.target.value)} placeholder="Получатель письма" />
                <select value={automationDocumentId} onChange={(e) => setAutomationDocumentId(Number(e.target.value))}>
                  <option value={0}>Без опорного документа</option>
                  {documentRows.map((document) => <option value={document.id} key={document.id}>{document.name}</option>)}
                </select>
                <input value={automationSubject} onChange={(e) => setAutomationSubject(e.target.value)} placeholder="Тема письма" />
                <textarea value={automationBody} onChange={(e) => setAutomationBody(e.target.value)} placeholder="Текст письма" />
                <input value={automationTaskTitle} onChange={(e) => setAutomationTaskTitle(e.target.value)} placeholder="Название контрольной задачи" />
                <button onClick={createAutomationRule}>Создать ежемесячный регламент</button>
              </div>
              <small>Сначала выберите договор, затем укажите email получателя. Переменные: {"{project}"}, {"{contract}"}, {"{month}"}, {"{next_month}"}, {"{date}"}.</small>
              <div className="automation-list">
                {automationRules.map((rule) => {
                  const latestRun = rule.runs[0];
                  return <article key={rule.id}>
                    <div>
                      <span className={`draft-status ${rule.active ? "ready" : "completed"}`}>{rule.active ? "Активен" : "Пауза"}</span>
                      <strong>{rule.name}</strong>
                      <small>Каждого {rule.day_of_month} числа · следующий запуск {new Date(`${rule.next_run_on}T00:00:00`).toLocaleDateString("ru-RU")} · {rule.recipient_to}</small>
                      <small>{rule.contract_id ? `Договор привязан · ` : ""}{rule.source_document_id ? "Опорный документ привязан" : "Без опорного документа"}</small>
                      {latestRun && <span className="automation-result">
                        Последняя подготовка: {new Date(`${latestRun.scheduled_for}T00:00:00`).toLocaleDateString("ru-RU")} · задача и письмо ожидают проверки
                      </span>}
                    </div>
                    <div className="automation-actions">
                      <button onClick={() => runAutomationNow(rule)}>Подготовить сейчас</button>
                      {latestRun?.task_id && <button className="secondary" onClick={() => setActive("Задачи")}>Открыть задачу</button>}
                      {latestRun?.response_draft_id && <button className="secondary" onClick={() => setActive("Письма")}>Открыть черновик</button>}
                      <button className="secondary" onClick={() => setAutomationActive(rule, !rule.active)}>{rule.active ? "Приостановить" : "Включить"}</button>
                    </div>
                  </article>;
                })}
                {!automationRules.length && <p>Регламентов пока нет. Заполните форму выше — например, ежемесячное письмо на пропуска.</p>}
              </div>
            </section>}
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
              {visibleInbox.some((item) => !item.context_confirmed) && (
                <div className="inbox-bulk card">
                  <label>
                    <input
                      type="checkbox"
                      checked={visibleInbox.filter((item) => !item.context_confirmed).every((item) => selectedInboxIds.includes(item.id))}
                      onChange={(e) => {
                        const ids = visibleInbox.filter((item) => !item.context_confirmed).map((item) => item.id);
                        setSelectedInboxIds((current) => e.target.checked
                          ? Array.from(new Set([...current, ...ids]))
                          : current.filter((id) => !ids.includes(id)));
                      }}
                    />
                    Выбрать все нераспределённые ({visibleInbox.filter((item) => !item.context_confirmed).length})
                  </label>
                  <select value={bulkInboxProjectId || projectId} onChange={(e) => {
                    setBulkInboxProjectId(Number(e.target.value));
                    setBulkInboxContractId(0);
                  }}>
                    {projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
                  </select>
                  <select
                    value={bulkInboxContractId}
                    disabled={(bulkInboxProjectId || projectId) !== projectId}
                    onChange={(e) => setBulkInboxContractId(Number(e.target.value))}
                  >
                    <option value={0}>Без договора</option>
                    {contracts.map((contract) => (
                      <option value={contract.id} key={contract.id}>{contract.number} — {contract.title}</option>
                    ))}
                  </select>
                  <button disabled={!selectedInboxIds.length} onClick={confirmMessageContextBulk}>
                    Перенести выбранные ({selectedInboxIds.length})
                  </button>
                  <small>Переносятся письма и связанные предложения задач, рисков и ответов. Письма в Gmail не изменяются.</small>
                </div>
              )}
              {visibleInbox
                .map((message) => {
                  const expanded = expandedInboxId === message.id;
                  return (
                  <article className={`card inbox-card ${expanded ? "expanded" : "collapsed"}`} key={message.id}>
                    <div className="inbox-head">
                      {!message.context_confirmed && (
                        <input
                          className="inbox-select"
                          type="checkbox"
                          aria-label={`Выбрать письмо ${message.source_name}`}
                          checked={selectedInboxIds.includes(message.id)}
                          onChange={(e) => setSelectedInboxIds((current) => e.target.checked
                            ? Array.from(new Set([...current, message.id]))
                            : current.filter((id) => id !== message.id))}
                        />
                      )}
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
                            value={message.project_id}
                            onChange={(e) =>
                              setInbox((rows) =>
                                rows.map((row) =>
                                  row.id === message.id
                                    ? { ...row, project_id: Number(e.target.value), contract_id: undefined }
                                    : row,
                                ),
                              )
                            }
                          >
                            {projects.map((project) => (
                              <option value={project.id} key={project.id}>{project.name}</option>
                            ))}
                          </select>
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
                            {(message.project_id === projectId ? contracts : []).map((contract) => (
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
                            {attachment.imported ? (
                              <button className="imported" onClick={() => setActive("Документы")}>Уже в документах</button>
                            ) : attachment.attachment_id ? (
                              <button onClick={() => importInboxAttachment(message, index)}>Импортировать и проанализировать</button>
                            ) : null}
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
                      <div className="project-actions">
                        <button onClick={() => activateProject(item.id)}>
                          {item.id === projectId ? "Открыть рабочий центр" : "Переключиться на проект"}
                        </button>
                        <button className="secondary" onClick={() => cleanupProjectCopies(item)} title="Переместить созданные системой копии в корзину Google Drive">
                          <Trash2 /> Очистить копии
                        </button>
                        <button className="danger" onClick={() => archiveProject(item)} title="Скрыть проект без удаления данных">
                          <Archive /> Архивировать
                        </button>
                      </div>
                    </article>
                  );
                })}
            </section>
          </div>
        </section>
      )}
      {(active === "Документы" || active === "Центр знаний") && (
        <section
          className={`documents-overlay ${collapsed ? "collapsed" : ""}`}
        >
          <div className="documents-layout">
            <div className="card">
              <div className="card-head">
                <div>
                  <h2>{active === "Центр знаний" ? "Центр знаний" : "Реестр документов"}</h2>
                  <p>
                    {active === "Центр знаний"
                      ? "Поиск по названиям, сводкам и извлечённому тексту"
                      : `Найдено: ${visibleDocuments.length}`}
                  </p>
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
