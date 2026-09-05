import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api/client";
import { Login } from "./auth/Login";
import { requestedProjectId, useProjectSelection } from "./context/useProjectSelection";
import { useStoragePicker } from "./modules/integrations/useStoragePicker";
import { useFinanceController } from "./modules/finance/useFinanceController";
import { FinanceModule } from "./modules/finance/FinanceModule";
import { FinanceOperations } from "./modules/finance/FinanceOperations";
import { ContextualAssistant } from "./modules/ai-secretary/ContextualAssistant";
import { DailyBriefingPanel, type DailyBriefing } from "./modules/ai-secretary/DailyBriefingPanel";
import { messageWorkflowClass, messageWorkflowLabel, type MessageWorkflowState } from "./modules/ai-secretary/messageWorkflow";
import { ProjectLaunchWizard } from "./modules/project-launch/ProjectLaunchWizard";
import { IntegrationsModule, type IntegrationItem, type SystemState } from "./modules/integrations/IntegrationsModule";
import { ContractsModule } from "./modules/contracts/ContractsModule";
import { ContractDocumentPicker } from "./modules/contracts/ContractDocumentPicker";
import { buildContractTree } from "./modules/contracts/contractTree";
import { ContractScheme, type SchemeDocument } from "./modules/contracts/ContractScheme";
import { ContractBulkImportWizard, type BulkContractProposal } from "./modules/contracts/ContractBulkImportWizard";
import { NotificationsModule, type NotificationItem } from "./modules/notifications/NotificationsModule";
import { TodayModule } from "./modules/today/TodayModule";
import { InboxModule } from "./modules/inbox/InboxModule";
import { EmailCompensationCard, type EmailCompensationOffer } from "./modules/inbox/EmailCompensationCard";
import { EvidencePanel, type EvidenceRef } from "./modules/evidence/EvidencePanel";
import { DocumentsModule, type DocumentCard as DocumentDetailModel } from "./modules/documents/DocumentsModule";
import { ProposalsModule, type Proposal, type ProposalAction } from "./modules/proposals/ProposalsModule";
import { AuditModule, type AuditRow } from "./modules/audit/AuditModule";
import { ObligationsModule, type ObligationRow } from "./modules/obligations/ObligationsModule";
import { MeetingsModule, type MeetingRow } from "./modules/meetings/MeetingsModule";
import { ProjectSearchResults, type ProjectSearchHit } from "./modules/search/ProjectSearchResults";
import { useProjectSearch } from "./modules/search/useProjectSearch";
import { AndroidBottomNav } from "./modules/android/AndroidBottomNav";
import { MobileDocumentUpload } from "./modules/android/MobileDocumentUpload";
import { ContactsModule, type ProjectContact } from "./modules/contacts/ContactsModule";
import { AnalyticsModule, type ProjectAnalytics } from "./modules/analytics/AnalyticsModule";
import { SettingsModule, type AIProjectPolicy, type ProcessingQueue } from "./modules/settings/SettingsModule";
import { TasksModule, type TaskHistoryRow, type TaskRow } from "./modules/tasks/TasksModule";
import { GovernanceModule, type DecisionRow, type RiskRow } from "./modules/governance/GovernanceModule";
import { ManagementCenter } from "./modules/management";
import { ForecastPanel, useForecast } from "./modules/forecast";
import { SupplyCenter } from "./modules/supply";
import { waitForSafeCopyCleanup } from "./modules/projects/safeCopyCleanup";
import { formatMoney } from "./utils/numberFormat";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
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
  Route,
  RotateCcw,
  Search,
  Settings,
  ShieldCheck,
  Users,
  Wallet,
  Archive,
  Trash2,
  TimerReset,
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
  parent_external_id?: string;
  mime_type?: string;
  source: string;
  status: string;
  current_version: number;
  summary?: string;
  source_url?: string;
};
type DocumentDetail = DocumentDetailModel;
type ContractSourceCandidate = {
  document_id: number;
  name: string;
  source: string;
  mime_type?: string;
  score: number;
  reasons: string[];
  text_ready: boolean;
};
const MAX_DROPPED_CONTRACT_BYTES = 10 * 1024 * 1024;
function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Не удалось прочитать ${file.name}`));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(file);
  });
}
type ContractFinancialCheck = {
  applied: string[];
  mismatches: { field: string; label: string; current: string; extracted: string; evidence?: string }[];
  terms: { amount?: string; advance_amount?: string; retention_percent?: string };
  sourceName?: string;
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
  recipient_to?: string;
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
type CurrentUser = {
  id: number;
  name: string;
  email: string;
  is_admin: boolean;
};
const PROJECT_ROLE_LEVEL: Record<string, number> = {
  viewer: 10,
  member: 20,
  editor: 30,
  manager: 40,
  owner: 50,
};
type ContractRow = {
  id: number;
  record_version: number;
  number: string;
  title: string;
  counterparty?: string;
  contract_kind?: "prime_reference" | "customer" | "revenue_subcontract" | "downstream_subcontract" | "supply";
  parent_contract_id?: number;
  amount?: number;
  advance_amount?: number;
  retention_percent?: number;
  warranty_until?: string;
  signed_at?: string;
  status: string;
  source_document_id?: number;
  notes?: string;
  linked_documents?: SchemeDocument[];
  version_history?: { id: number; sequence: number; event: string; changed_fields: string[]; occurred_at: string }[];
  analysis?: {
    source_ready: boolean;
    tasks: number;
    obligations: number;
    risks: number;
    decisions: number;
  };
};
type ContractEditDraft = {
  number: string; title: string; counterparty: string; amount: string;
  advanceAmount: string; retentionPercent: string; signedAt: string; status: string;
};
type AnalysisResult = {
  status: string;
  mode?: string;
  documents?: number;
  tasks?: number;
  risks?: number;
  drafts?: number;
  copy_folder_name?: string;
  organizer_session_id?: number;
  source_item_count?: number;
  copy_item_count?: number;
  error?: string;
};
type Snapshot = {
  id: number;
  provider?: string;
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
  provider?: string;
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
  job_id?: number;
  job_status?: string;
  job_progress?: number;
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
  recipient_to?: string;
  is_corrective_follow_up?: boolean;
  email_compensation?: EmailCompensationOffer | null;
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
  analysis_required?: boolean;
  workflow_state?: MessageWorkflowState;
  workflow_reason?: string;
  created_at: string;
  tasks: InboxTask[];
  drafts: InboxDraft[];
  risks: InboxRisk[];
  completion_suggestions: {
    id: number;
    task_id: number;
    task_title: string;
    task_status: string;
    confidence: number;
    evidence: string;
    status: string;
  }[];
  evidence_refs?: EvidenceRef[];
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
type NotificationRow = NotificationItem;
const items = [
  [CalendarDays, "Сегодня"],
  [LayoutDashboard, "Рабочий центр"],
  [Route, "Запуск проекта"],
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

function snapshotActionError(error: unknown) {
  if (error instanceof ApiError && error.status === 409) {
    return error.message.includes("queued, running or retrying")
      ? "Задание уже находится в очереди или выполняется. Дождитесь завершения и обновите состояние."
      : "Операция недоступна в текущем состоянии. Обновите данные проекта и проверьте подключение папки.";
  }
  return error instanceof Error ? error.message : "Не удалось выполнить операцию";
}

export function App() {
  const { projectId, projectIdRef, rememberProject, initialProjectId } = useProjectSelection();
  const [ready, setReady] = useState(false),
    [collapsed, setCollapsed] = useState(false),
    [mobile, setMobile] = useState(false),
    [online, setOnline] = useState(navigator.onLine),
    [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(null);
  const [mobileUploadOpen, setMobileUploadOpen] = useState(false);
  const [active, setActive] = useState(() => new URLSearchParams(window.location.search).get("oauth") === "connected" ? "Запуск проекта" : "Рабочий центр"),
    [query, setQuery] = useState(""),
    [newProjectName, setNewProjectName] = useState(""),
    [newContractNumber, setNewContractNumber] = useState(""),
    [newContractTitle, setNewContractTitle] = useState(""),
    [newCounterparty, setNewCounterparty] = useState(""),
    [newContractKind, setNewContractKind] = useState<"prime_reference" | "customer" | "revenue_subcontract" | "downstream_subcontract" | "supply">("customer"),
    [newParentContractId, setNewParentContractId] = useState(0),
    [newContractAmount, setNewContractAmount] = useState(""),
    [newAdvanceAmount, setNewAdvanceAmount] = useState(""),
    [newRetentionPercent, setNewRetentionPercent] = useState(""),
    [newContractSignedAt, setNewContractSignedAt] = useState(""),
    [contractDocumentTabs, setContractDocumentTabs] = useState<Record<number, "recommended" | "server" | "upload" | "google">>({}),
    [contractDocumentQueries, setContractDocumentQueries] = useState<Record<number, string>>({}),
    [contractCatalogOpen, setContractCatalogOpen] = useState<Record<number, boolean>>({}),
    [contractStructureDrafts, setContractStructureDrafts] = useState<Record<number, { kind: string; parentId: number }>>({}),
    [contractEditDrafts, setContractEditDrafts] = useState<Record<number, ContractEditDraft>>({}),
    [contractSourceCandidates, setContractSourceCandidates] = useState<Record<number, ContractSourceCandidate[]>>({}),
    [droppedContractProposals, setDroppedContractProposals] = useState<BulkContractProposal[]>([]),
    [contractFinancialChecks, setContractFinancialChecks] = useState<Record<number, ContractFinancialCheck>>({}),
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
    [projectContacts, setProjectContacts] = useState<ProjectContact[]>([]),
    [mailView, setMailView] = useState<"inbox" | "companies">("inbox"),
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
    [proposals, setProposals] = useState<Proposal[]>([]),
    [auditLogs, setAuditLogs] = useState<AuditRow[]>([]),
    [members, setMembers] = useState<MemberRow[]>([]),
    [googleState, setGoogleState] = useState<GoogleState | null>(null),
    [aiPolicy, setAiPolicy] = useState<AIProjectPolicy | null>(null),
    [processingQueue, setProcessingQueue] = useState<ProcessingQueue | null>(null),
    [systemState, setSystemState] = useState<SystemState | null>(null),
    [currentUser, setCurrentUser] = useState<CurrentUser | null>(null),
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
  const [contractDropStatus, setContractDropStatus] = useState("");
  const [dailyBriefing, setDailyBriefing] = useState<DailyBriefing | null>(null);
  const [integrationItems, setIntegrationItems] = useState<IntegrationItem[]>([]);
  const [copyCleanupResults, setCopyCleanupResults] = useState<Record<number, { count: number; message: string }>>({});
  const picker = useStoragePicker<DriveFolder>(projectId, projectIdRef);
  const { folders, setFolders, busyFolder, setBusyFolder } = picker;
  const showSources = picker.isOpen;
  const sourceFolderId = picker.folderId;
  const sourceProvider = picker.context?.provider;
  const sourceBreadcrumbs = picker.breadcrumbs;
  const {
    finance, financeCandidates, financeStructuredPreview, financeStructuredRows,
    selectedFinanceContractId, financeKind, financeTitle, financeAmount, financeDate,
    financeExtra, financeSourceDocumentId, financeScheduleItemId, financeBudgetLineId,
    setFinanceStructuredPreview, setFinanceStructuredRows, setSelectedFinanceContractId,
    setFinanceKind, setFinanceTitle, setFinanceAmount, setFinanceDate, setFinanceExtra,
    setFinanceSourceDocumentId, setFinanceScheduleItemId, setFinanceBudgetLineId,
    loadFinance, prepareFinanceItem, useFinanceCandidate, prepareDroppedFinanceDocument, importStructuredFinance,
    addFinanceItem, confirmFinance, cloneBaseline, confirmCashPayment, correctCashPayment, updateScheduleActual,
  } = useFinanceController({ ready, projectId, setNotice, setError });
  const forecast = useForecast(projectId || null, ready && active === "Исполнение и финансы");
  const [acknowledgedForecastId, setAcknowledgedForecastId] = useState<string | null>(null);
  const loadSequenceRef = useRef(0);

  async function activateProject(id: number) {
    picker.close();
    rememberProject(id);
    setActive("Рабочий центр");
    await load(id);
  }

  async function load(preferredProjectId?: number) {
    const loadSequence = ++loadSequenceRef.current;
    const selectionAtStart = projectIdRef.current;
    const requested = preferredProjectId ?? selectionAtStart;
    try {
      setError("");
      const p = await api("/projects/");
      if (loadSequence !== loadSequenceRef.current || projectIdRef.current !== selectionAtStart) return;
      setProjects(p.projects);
      const id = requestedProjectId(p.projects, requested);
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
          contactData,
          briefingData,
        ] = await Promise.all([
          api(`/dashboard/project?project_id=${id}`),
          api(`/projects/${id}/snapshots`),
          api(`/tasks?project_id=${id}`),
          api(`/governance/risks?project_id=${id}`),
          api(`/governance/decisions?project_id=${id}`),
          api(`/projects/${id}/documents?limit=5000`),
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
          api(`/project-contacts?project_id=${id}`).catch(() => ({ contacts: [] })),
          api(`/ai-secretary/daily-briefing?project_id=${id}`).catch(() => null),
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
        setProjectContacts(contactData.contacts);
        setDailyBriefing(briefingData);
      }
    } catch (e) {
      if (loadSequence === loadSequenceRef.current && projectIdRef.current === selectionAtStart) setError((e as Error).message);
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
      await activateProject(created.id);
      setActive("Запуск проекта");
      setNotice(`Проект «${created.name}» создан. Выберите, как начать работу: с существующей папки или с новой постоянной структуры.`);
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
        setCopyCleanupResults((current) => ({
          ...current,
          [project.id]: { count: 0, message: "Управляемых копий для очистки нет. Можно архивировать проект." },
        }));
        setNotice("Безопасных копий PU Workspace для очистки нет");
        return;
      }
      const confirmation = window.prompt(
        `Будут перемещены в корзину хранилища только ${summary.count} управляемых копий PU Workspace. Оригиналы не затрагиваются. Для подтверждения введите точное название проекта:`,
      );
      if (confirmation === null) return;
      const commandKey = crypto.randomUUID();
      const queued = await api(`/projects/${project.id}/safe-copies/trash`, {
        method: "POST",
        headers: { "Idempotency-Key": commandKey },
        body: JSON.stringify({
          confirmation,
          expected_cleanup_version: summary.cleanup_version,
          command_key: commandKey,
        }),
      });
      setNotice(`Очистка ${queued.count} управляемых копий запущена. Оригиналы не затрагиваются.`);
      const result = await waitForSafeCopyCleanup(api, project.id, queued.job_id);
      setCopyCleanupResults((current) => ({
        ...current,
        [project.id]: {
          count: result.trashed ?? queued.count,
          message: result.message || "Копии удалены, можете архивировать проект",
        },
      }));
      setNotice(`Копии удалены: ${result.trashed}. Можете архивировать проект. Оригиналы не изменены.`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function connectGoogle() {
    picker.close();
    try {
      const result = await api(`/projects/${projectId}/google/auth`);
      window.location.href = result.authorization_url;
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function connectStorageProvider(provider: string) {
    picker.close();
    if (provider !== "yandex_disk") return connectGoogle();
    try {
      const result = await api(`/projects/${projectId}/yandex/auth`);
      window.location.href = result.authorization_url;
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function openProviderSources(provider: string) {
    await picker.open(provider);
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
      setError(snapshotActionError(e));
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
          contract_kind: newContractKind,
          parent_contract_id: ["prime_reference", "customer"].includes(newContractKind) ? undefined : newParentContractId,
          amount: newContractAmount ? Number(newContractAmount) : undefined,
          advance_amount: newAdvanceAmount ? Number(newAdvanceAmount) : undefined,
          retention_percent: newRetentionPercent ? Number(newRetentionPercent) : undefined,
          signed_at: newContractSignedAt || undefined,
        }),
      });
      setNewContractNumber("");
      setNewContractTitle("");
      setNewCounterparty("");
      setNewContractKind("customer");
      setNewParentContractId(0);
      setNewContractAmount("");
      setNewAdvanceAmount("");
      setNewRetentionPercent("");
      setNewContractSignedAt("");
      if (created.contract_kind !== "prime_reference") setSelectedFinanceContractId(created.id);
      setNotice(created.contract_kind === "prime_reference"
        ? "Генподрядный договор добавлен как контекст. Теперь добавьте наш субподрядный договор и свяжите его с генподрядным."
        : "Финансовый договор добавлен; черновик ГПР создан, контур бюджета и ДДС готов к заполнению");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function linkContractDocument(contractId: number, documentId: number) {
    try {
      const expected = contracts.find((item) => item.id === contractId)?.record_version;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      await api(`/projects/${projectId}/contracts/${contractId}`, {
        method: "PATCH",
        body: JSON.stringify({ expected_record_version: expected, source_document_id: documentId || null }),
      });
      setNotice(documentId ? "Документ-источник привязан к договору" : "Связь с документом снята");
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }
  async function linkContractParent(contractId: number, contractKind: string, parentContractId: number) {
    try {
      const expected = contracts.find((item) => item.id === contractId)?.record_version;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      await api(`/projects/${projectId}/contracts/${contractId}`, {
        method: "PATCH",
        body: JSON.stringify({ expected_record_version: expected, contract_kind: contractKind, parent_contract_id: parentContractId || null }),
      });
      setNotice("Вышестоящий договор сохранён — дерево перестроено");
      setContractStructureDrafts((current) => { const next = { ...current }; delete next[contractId]; return next; });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function discoverBulkContracts(documentIds: number[]): Promise<BulkContractProposal[]> {
    const result = await api(`/projects/${projectId}/contracts/discover-bulk`, {
      method: "POST", body: JSON.stringify({ document_ids: documentIds }),
    });
    const proposals = result.proposals || [];
    if (!proposals.length && result.rejected_count) {
      throw new Error(`Самостоятельные договоры не найдены. Исключены приложения и связанные файлы: ${result.rejected_count}.`);
    }
    if (result.rejected_count) setNotice(`Найдено договоров: ${proposals.length}. Приложений и связанных файлов исключено: ${result.rejected_count}.`);
    return proposals;
  }
  async function prepareDroppedContracts(documentIds: number[], parentContractId?: number) {
    try {
      setError("");
      const proposals = await discoverBulkContracts(documentIds);
      if (!proposals.length) throw new Error("Файл распознан, но самостоятельный договор не найден. Откройте массовый мастер и выберите тип вручную.");
      setDroppedContractProposals(proposals.map((item) => parentContractId ? {
        ...item,
        contract_kind: ["supply", "downstream_subcontract"].includes(item.contract_kind) ? item.contract_kind : "downstream_subcontract",
        parent_document_id: undefined,
        parent_contract_id: parentContractId,
        evidence: [...item.evidence, "вышестоящий договор выбран перетаскиванием на схеме"],
      } : item));
      const roles = proposals.map((item) => item.contract_kind === "prime_reference" ? "генподряд" : item.contract_kind === "supply" ? "поставщик" : item.contract_kind === "downstream_subcontract" ? "субподряд" : "прямой договор").join(", ");
      setContractDropStatus(`Анализ завершён: найдено ${proposals.length}; предложенная роль: ${roles}. Открыт мастер проверки — подтвердите тип и связь.`);
    } catch (reason) { const message = (reason as Error).message; setContractDropStatus(`Анализ завершён с замечанием: ${message}`); setError(message); }
  }
  async function uploadDroppedContracts(files: File[], parentContractId?: number) {
    const supported = files.filter((file) => /\.(pdf|docx?|xlsx?|txt|csv|png|jpe?g|tiff?|bmp|webp)$/i.test(file.name));
    const oversized = supported.find((file) => file.size > MAX_DROPPED_CONTRACT_BYTES);
    if (!supported.length) { setError("Выберите договор в PDF, Word, Excel, CSV либо фото/скан JPG, PNG или TIFF."); return; }
    if (oversized) { setError(`${oversized.name}: файл больше 10 МБ`); return; }
    try {
      setError(""); setContractDropStatus(`Получено файлов: ${supported.length}. Загружаю и запускаю OCR…`); setNotice(`Загружаю и анализирую договоров: ${supported.length}…`);
      const payload = await Promise.all(supported.slice(0, 50).map(async (file) => ({ path: file.name, mime_type: file.type || "application/octet-stream", content_base64: await fileBase64(file) })));
      const result = await api("/local-upload/analyze", { method: "POST", body: JSON.stringify({ project_id: projectId, files: payload }) });
      const documentIds = (result.documents || []).map((item: { id: number }) => item.id);
      if (!documentIds.length) throw new Error(result.skipped?.[0]?.reason || "Текст договора не извлечён");
      setContractDropStatus(`OCR завершён: документов ${documentIds.length}. Определяю роль и место в дереве…`);
      await load(); await prepareDroppedContracts(documentIds, parentContractId);
    } catch (reason) { const message = (reason as Error).message; setContractDropStatus(`Загрузка не завершена: ${message}`); setError(message); }
  }
  async function uploadContractApplications(files: File[], contractId: number) {
    const supported = files.filter((file) => /\.(pdf|docx?|xlsx?|txt|csv|png|jpe?g|tiff?|bmp|webp)$/i.test(file.name));
    const oversized = supported.find((file) => file.size > MAX_DROPPED_CONTRACT_BYTES);
    if (!supported.length) { setError("Выберите приложение в PDF, Word, Excel, CSV либо фото/скан JPG, PNG или TIFF."); return; }
    if (oversized) { setError(`${oversized.name}: файл больше 10 МБ`); return; }
    try {
      setError(""); setNotice(`Загружаю приложения к договору: ${supported.length}…`);
      const payload = await Promise.all(supported.slice(0, 50).map(async (file) => ({ path: file.name, mime_type: file.type || "application/octet-stream", content_base64: await fileBase64(file) })));
      const uploaded = await api("/local-upload/analyze", { method: "POST", body: JSON.stringify({ project_id: projectId, files: payload }) });
      const documentIds = (uploaded.documents || []).map((item: { id: number }) => item.id);
      if (!documentIds.length) throw new Error(uploaded.skipped?.[0]?.reason || "Текст приложений не извлечён");
      const expected = contracts.find((item) => item.id === contractId)?.record_version;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      await api(`/projects/${projectId}/contracts/${contractId}/applications`, { method: "POST", body: JSON.stringify({ expected_record_version: expected, document_ids: documentIds }) });
      const checked = await api(`/projects/${projectId}/contracts/${contractId}/analyze-package`, { method: "POST" });
      const direction = checked.financial_direction === "inflow" ? "приход" : checked.financial_direction === "outflow" ? "затраты" : "контекст без движения денег";
      setNotice(`Пакет договора проверен: документов ${checked.documents}, ошибок/расхождений ${checked.issue_count}, финансовых предложений ${checked.financial_entries} (${direction}). Оплаты не подтверждены автоматически.`);
      await load();
    } catch (reason) { setError((reason as Error).message); }
  }
  async function uploadContractFinance(files: File[], contractId: number, kind: "schedule" | "budget" | "cash-flow") {
    const supported = files.filter((file) => /\.(xlsx?|csv|docx?|pdf|txt|png|jpe?g|tiff?|bmp|webp)$/i.test(file.name));
    const oversized = supported.find((file) => file.size > MAX_DROPPED_CONTRACT_BYTES);
    if (!supported.length) { setError("Выберите Excel, CSV, Word, PDF либо фото/скан JPG, PNG или TIFF."); return; }
    if (oversized) { setError(`${oversized.name}: файл больше 10 МБ`); return; }
    try {
      const label = kind === "schedule" ? "ГПР" : kind === "budget" ? "бюджет" : "ДДС";
      setError(""); setNotice(`Загружаю и разбираю ${label}: ${supported.length} файл(ов)…`);
      const payload = await Promise.all(supported.slice(0, 50).map(async (file) => ({ path: file.name, mime_type: file.type || "application/octet-stream", content_base64: await fileBase64(file) })));
      const uploaded = await api("/local-upload/analyze", { method: "POST", body: JSON.stringify({ project_id: projectId, files: payload }) });
      const documents = (uploaded.documents || []) as { id: number; name: string }[];
      if (!documents.length) throw new Error(uploaded.skipped?.[0]?.reason || `Не удалось извлечь таблицу ${label}`);
      const expected = contracts.find((item) => item.id === contractId)?.record_version;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      await api(`/projects/${projectId}/contracts/${contractId}/documents`, {
        method: "POST",
        body: JSON.stringify({ expected_record_version: expected, document_ids: documents.map((item) => item.id), role: kind === "cash-flow" ? "cash_flow" : kind }),
      });
      await prepareDroppedFinanceDocument(documents[0].id, documents[0].name, kind, contractId);
      setActive("Исполнение и финансы");
      await loadFinance();
    } catch (reason) { setError((reason as Error).message); }
  }
  async function importBulkContracts(proposals: BulkContractProposal[]): Promise<number> {
    const createdByDocument = new Map<number, number>();
    const pending = [...proposals];
    let created = 0;
    const createdContractIds: number[] = [];
    while (pending.length) {
      const index = pending.findIndex((item) => !item.parent_document_id || createdByDocument.has(item.parent_document_id));
      if (index < 0) throw new Error("В выбранной структуре найден цикл. Проверьте вышестоящие договоры.");
      const [item] = pending.splice(index, 1);
      const parentId = item.parent_contract_id || (item.parent_document_id ? createdByDocument.get(item.parent_document_id) : undefined);
      const row = await api(item.already_linked && item.linked_contract_id
        ? `/projects/${projectId}/contracts/${item.linked_contract_id}`
        : `/projects/${projectId}/contracts`, {
        method: item.already_linked && item.linked_contract_id ? "PATCH" : "POST",
        body: JSON.stringify({
          ...(item.already_linked && item.linked_contract_id ? {
            expected_record_version: contracts.find((contract) => contract.id === item.linked_contract_id)?.record_version,
          } : {}),
          number: item.number.trim(), title: item.title.trim(), counterparty: item.counterparty?.trim() || undefined,
          contract_kind: item.contract_kind, parent_contract_id: parentId,
          ...(!item.already_linked ? { source_document_id: item.document_id } : {}),
        }),
      });
      createdByDocument.set(item.document_id, row.id); created += item.already_linked ? 0 : 1;
      createdContractIds.push(row.id);
    }
    let analyzed = 0;
    for (const contractId of createdContractIds) {
      try { await api(`/projects/${projectId}/contracts/${contractId}/analyze`, { method: "POST" }); analyzed += 1; }
      catch { /* Карточка и источник уже сохранены; повтор анализа доступен в карточке. */ }
    }
    const updated = proposals.filter((item) => item.already_linked).length;
    setNotice(`Создано договоров: ${created}. Обновлено существующих: ${updated}. Полностью проанализировано: ${analyzed}. Дерево построено; проверьте связи на схеме.`);
    await load();
    return created;
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
      const financial = result.financial_check;
      setContractFinancialChecks((current) => ({ ...current, [contractId]: { ...financial, sourceName: result.source?.name } }));
      const linked = result.source?.automatically_linked ? ` Автоматически прикреплён файл «${result.source.name}».` : "";
      const applied = financial?.applied?.length ? ` Заполнены условия: ${financial.applied.join(", ")}.` : "";
      const mismatches = financial?.mismatches?.length ? ` ВНИМАНИЕ: расхождений в финансовых условиях — ${financial.mismatches.length}; проверьте карточку договора.` : " Финансовые расхождения не обнаружены.";
      setNotice(`Договор проанализирован.${linked}${applied}${mismatches} Задач ${result.analysis.tasks}, обязательств ${result.analysis.obligations}, строк графика платежей ${result.created?.payment_schedule || 0}.`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function beginContractEdit(item: ContractRow) {
    setContractEditDrafts((current) => ({ ...current, [item.id]: {
      number: item.number, title: item.title, counterparty: item.counterparty || "",
      amount: item.amount?.toString() || "", advanceAmount: item.advance_amount?.toString() || "",
      retentionPercent: item.retention_percent?.toString() || "", signedAt: item.signed_at?.slice(0, 10) || "",
      status: item.status,
    } }));
  }
  async function saveContractEdit(contractId: number) {
    const draft = contractEditDrafts[contractId];
    if (!draft?.number.trim() || !draft.title.trim()) return;
    try {
      setError("");
      const expected = contracts.find((item) => item.id === contractId)?.record_version;
      if (!expected) throw new Error("Версия договора не загружена. Обновите карточку.");
      await api(`/projects/${projectId}/contracts/${contractId}`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_record_version: expected,
          number: draft.number.trim(), title: draft.title.trim(), counterparty: draft.counterparty.trim() || null,
          amount: draft.amount ? Number(draft.amount) : null,
          advance_amount: draft.advanceAmount ? Number(draft.advanceAmount) : null,
          retention_percent: draft.retentionPercent ? Number(draft.retentionPercent) : null,
          signed_at: draft.signedAt || null, status: draft.status,
        }),
      });
      setContractEditDrafts((current) => { const next = { ...current }; delete next[contractId]; return next; });
      setNotice("Договор обновлён. Связанные документы, ГПР, бюджет и ДДС сохранены.");
      await load();
    } catch (e) { setError((e as Error).message); }
  }
  async function deleteContract(item: ContractRow) {
    try {
      const preview = await api(`/projects/${projectId}/contracts/${item.id}/deletion-preview`);
      if (preview?.can_delete === false) {
        const count = Object.values(preview.dependencies || {}).reduce((sum: number, value) => sum + Number(value || 0), 0);
        if (window.confirm(`У договора ${count} связанных объектов. Физическое удаление заблокировано. Архивировать договор с сохранением всех связей?`)) {
          await archiveContract(item);
        }
        return;
      }
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    const confirmation = window.prompt(
      `Удалить договор «${item.number}»? Исходные документы не удаляются. Введите точный номер договора:`,
    );
    if (confirmation === null) return;
    try {
      setError("");
      await api(`/projects/${projectId}/contracts/${item.id}`, {
        method: "DELETE", body: JSON.stringify({ confirmation, expected_record_version: item.record_version }),
      });
      setNotice(`Договор «${item.number}» удалён. Исходные документы сохранены.`);
      await load();
    } catch (e) { setError((e as Error).message); }
  }
  async function archiveContract(item: ContractRow) {
    try {
      setError("");
      await api(`/projects/${projectId}/contracts/${item.id}`, {
        method: "PATCH", body: JSON.stringify({ expected_record_version: item.record_version, status: "archived" }),
      });
      setNotice(`Договор «${item.number}» архивирован. Все документы, ГПР, ДДС и другие связи сохранены.`);
      await load();
    } catch (e) { setError((e as Error).message); }
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
  async function openSources(folderId?: string) {
    if (folderId === undefined) await picker.open();
    else await picker.navigate(folderId);
  }
  async function queueFolder(folder: DriveFolder) {
    await picker.confirm(folder);
  }
  async function refreshFolder(folder: DriveFolder) {
    const refreshed = await picker.confirm(folder, { refresh: true });
    if (refreshed) {
      setNotice(`Создан новый снимок «${folder.name}». Изменения на диске поставлены в очередь анализа.`);
    }
  }
  async function makePrimary(folder: DriveFolder) {
    const request = picker.capture();
    if (!request) return;
    try {
      setBusyFolder(folder.id);
      setError("");
      await api(`/projects/${request.context.project_id}/source-folders/${encodeURIComponent(folder.id)}/primary`, {
        method: "POST",
      });
      if (!request.current()) return;
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
      if (request.current()) setError((e as Error).message);
    } finally {
      if (request.current()) setBusyFolder("");
    }
  }
  async function analyzeFolder(folder: DriveFolder) {
    if (!folder.snapshot_id) return;
    const request = picker.capture();
    if (!request) return;
    try {
      setBusyFolder(folder.id);
      setError("");
      setNotice("");
      const result = await api(
        `/projects/${request.context.project_id}/snapshots/${folder.snapshot_id}/standardize`,
        { method: "POST" },
      );
      if (!request.current()) return;
      if (result.already_queued) {
        setFolders((items) =>
          items.map((item) =>
            item.id === folder.id ? { ...item, analyzed: result.status === "ready", analysis_status: result.status } : item,
          ),
        );
        setNotice(result.status === "ready"
          ? `${folder.name}: обработка завершена`
          : `${folder.name}: задание уже существует (${result.status})`);
      } else {
        setFolders((items) =>
          items.map((item) =>
            item.id === folder.id
              ? { ...item, analysis_status: "analyzing" }
              : item,
          ),
        );
        setNotice(
          `Создание безопасной копии, анализ и стандартизация «${folder.name}» поставлены в очередь. Страницу можно закрыть.`,
        );
      }
    } catch (e) {
      if (request.current()) setError((e as Error).message);
    } finally {
      if (request.current()) setBusyFolder("");
    }
  }
  async function prepareSourceChanges(folder: DriveFolder) {
    if (!folder.snapshot_id) return;
    const request = picker.capture();
    if (!request) return;
    try {
      setBusyFolder(folder.id);
      setError("");
      await api(`/projects/${request.context.project_id}/snapshots/${folder.snapshot_id}/analyze`, {
        method: "POST",
      });
      if (!request.current()) return;
      picker.close();
      setActive("Предложения");
      setNotice("Готовится таблица «Было → Станет» для рабочей папки. Обновите раздел через несколько секунд.");
      await load();
    } catch (e) {
      if (request.current()) setError(snapshotActionError(e));
    } finally {
      if (request.current()) setBusyFolder("");
    }
  }
  async function queueAllFolders() {
    const request = picker.capture();
    if (!request) return;
    try {
      setBusyAll(true);
      setError("");
      const result = await api(
        `/projects/${request.context.project_id}/source-folders/snapshot-queue-all`,
        { method: "POST" },
      );
      if (!request.current()) return;
      setNotice(
        result.queued
          ? `В очередь добавлено папок: ${result.queued}. Они будут обработаны последовательно.`
          : "Все доступные папки уже подключены или находятся в очереди.",
      );
      await picker.navigate();
      await load();
    } catch (e) {
      if (request.current()) setError((e as Error).message);
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
  async function reviewOutgoingCompletion(message: InboxMessage, suggestionId: number, status: "confirmed" | "rejected") {
    try {
      await api(`/ai-secretary/inbox/${message.id}/completion-suggestions/${suggestionId}`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      setNotice(status === "confirmed" ? "Задача отмечена выполненной по исходящему письму" : "Предложение отклонено; задача не изменена");
      await load();
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
          recipient_to: draft.recipient_to,
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
  async function proposeEmailCompensation(draft: InboxDraft, offer: EmailCompensationOffer) {
    if (!offer.source_etag) return;
    try {
      await api(`/response-drafts/${draft.id}/email-compensation/proposals`, {
        method: "POST",
        body: JSON.stringify({ expected_source_etag: offer.source_etag }),
      });
      setNotice("Корректирующий ответ подготовлен как черновик. Отправка требует отдельного подтверждения.");
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
  async function standardizeProposal(proposal: Proposal) {
    if (!window.confirm("Переименовать и разложить оставшиеся файлы в безопасной копии по единому стандарту? Оригиналы не изменятся."))
      return;
    try {
      setBusyProposal(proposal.id);
      const result = await api(
        `/organizer/proposals/${proposal.id}/standardize-copy`,
        { method: "POST" },
      );
      setNotice(
        `Стандартизация копии завершена: переименовано ${result.stats.renamed}, перемещено ${result.stats.moved}, пропущено ${result.stats.skipped}.`,
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
  async function applyApprovedToSource(proposal: Proposal) {
    const approved = proposal.actions.filter((action) =>
      ["approved", "edited"].includes(action.user_decision),
    ).length;
    const phrase = window.prompt(
      `Будут применены ${approved} подтверждённых изменений к РАБОЧЕЙ папке. Безопасная копия уже проверена. Введите APPLY_APPROVED_TO_SOURCE`,
    );
    if (phrase !== "APPLY_APPROVED_TO_SOURCE") return;
    try {
      setBusyProposal(proposal.id);
      const result = await api(
        `/organizer/proposals/${proposal.id}/apply-source-approved`,
        { method: "POST", body: JSON.stringify({ confirmation: phrase }) },
      );
      setNotice(
        `Рабочая папка стандартизирована: переименовано ${result.stats.renamed}, перемещено ${result.stats.moved}. Все операции записаны; доступен откат.`,
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
    const shortcut = new URLSearchParams(window.location.search).get("section");
    const sections: Record<string, string> = { today: "Сегодня", mail: "Письма", tasks: "Задачи" };
    if (shortcut && sections[shortcut]) {
      setActive(sections[shortcut]);
      window.history.replaceState({}, "", window.location.pathname);
    }
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
    if (active !== "Рабочий центр" || !showSources) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById("drive-source-picker")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, showSources]);
  useEffect(() => {
    if (!ready || !projectId || !processingQueue?.summary.active) return;
    const timer = window.setInterval(async () => {
      try {
        const queue = await api(`/projects/${projectId}/processing-queue`);
        if (projectIdRef.current !== projectId) return;
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
      picker.loading || busyFolder || picker.needsReopen || (!folders.some(
        (item) =>
          item.snapshot_status === "building" ||
          item.analysis_status === "analyzing",
      ) && picker.selection?.status !== "building" && picker.selection?.analysis_status !== "analyzing")
    )
      return;
    const timer = window.setInterval(() => {
      openSources(sourceFolderId);
      load();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [
    ready,
    projectId,
    showSources,
    sourceFolderId,
    picker.loading, busyFolder, picker.needsReopen, picker.selection?.status, picker.selection?.analysis_status,
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
        result.proposal_state === "invalid_source" || result.origin_status === "invalid_source"
          || result.origin_reason === "meeting_source_binding_required"
          ? "Протокол сохранён. Требуется привязка протокола к источнику и его неизменяемой версии; эта привязка пока не реализована. Создание и подтверждение предложений по протоколу недоступны."
          : result.confirmation_available === false || result.origin_status !== undefined || result.origin_reason !== undefined
          ? "Протокол сохранён. Подтверждение предложений недоступно: статус источника не подтверждён. Обновите данные или обратитесь к администратору."
          : result.proposal_state === "awaiting_evidence"
          ? "Протокол сохранён. Проверьте предложения и доказательства перед созданием задач и решений."
          : "Протокол сохранён.",
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
  const { hits: projectSearchHits } = useProjectSearch(projectId, query);
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
  function openContextualAssistant(prompt: string) {
    setIncomingName(`Контекстная помощь · ${active}`);
    setIncomingText(prompt);
    setActive("AI Secretary");
    window.setTimeout(() => document.querySelector<HTMLTextAreaElement>(".inbox-compose textarea")?.focus(), 0);
  }
  const latestSnapshot =
    snapshots.find((item) => item.is_primary) || snapshots[0];
  const money = formatMoney;
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
  function openProjectSearchHit(hit: ProjectSearchHit) {
    setQuery("");
    if (hit.kind === "document") {
      const document = documentRows.find((item) => item.id === hit.id);
      setActive("Документы");
      if (document) void openDocument(document);
    } else if (hit.kind === "contract") {
      setActive("Договоры");
    } else if (hit.kind === "task") {
      setActive("Задачи");
    } else if (hit.kind === "obligation") setActive("Обязательства");
    else if (hit.kind === "risk" || hit.kind === "decision") setActive("Риски и решения");
    else if (hit.kind === "message") {
      setActive("Письма");
      setMailView("inbox");
      setExpandedInboxId(hit.id);
    } else setActive("Рабочий центр");
  }
  const currentProjectRole = members.find((member) => member.user_id === currentUser?.id)?.role || "";
  const currentProjectRoleLevel = PROJECT_ROLE_LEVEL[currentProjectRole] || 0;
  const canEditSupply = Boolean(currentUser?.is_admin || currentProjectRoleLevel >= PROJECT_ROLE_LEVEL.editor);
  const canManageSupply = Boolean(currentUser?.is_admin || currentProjectRoleLevel >= PROJECT_ROLE_LEVEL.manager);
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
              void api("/auth/logout", { method: "POST" }).finally(() => setReady(false));
            }}
          >
            <LogOut />
          </button>
        </div>
      </aside>
      {mobile && <button className="mobile-drawer-backdrop" aria-label="Закрыть меню" onClick={() => setMobile(false)} />}
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
              <ProjectSearchResults query={query} hits={projectSearchHits} onOpen={openProjectSearchHit} />
            </div>
            <select
              value={projectId}
              onChange={(e) => {
                const id = Number(e.target.value);
                picker.close();
                rememberProject(id);
                void load(id);
              }}
            >
              {projectId > 0 && !projects.some(project => project.id === projectId) && <option value={projectId}>Проект №{projectId} — ожидается загрузка</option>}
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
          {active === "Сегодня" && (
            <TodayModule
              projectName={projects.find((item) => item.id === projectId)?.name || ""}
              briefing={dailyBriefing}
              summary={summary}
              inboxAttention={inbox.filter((item) => !item.context_confirmed || item.status !== "completed").length}
              onOpen={setActive}
            />
          )}
          {active === "Запуск проекта" && (
            <ProjectLaunchWizard
              projectId={projectId}
              storageAuthorized={googleState?.authorized === true}
              onConnectStorage={() => void connectGoogle()}
              openSection={(section, target) => {
                if (target === "contacts") setMailView("companies");
                setActive(section);
                if (target === "source") void openSources();
              }}
            />
          )}
          {active === "Задачи" ? (
            <TasksModule
              tasks={tasks}
              filter={taskFilter}
              members={members}
              documents={documentRows}
              completionTaskId={completionTaskId}
              completionNote={completionNote}
              completionDocumentId={completionDocumentId}
              historyTaskId={taskHistoryId}
              history={taskHistory}
              onFilterChange={setTaskFilter}
              onAssign={(task, userId) => void assignTask(task, userId)}
              onApproveExternal={(task) => void approveExternal(task)}
              onUpdate={(task, status) => void updateTask(task, status)}
              onStartCompletion={startTaskCompletion}
              onCancelCompletion={() => setCompletionTaskId(0)}
              onCompletionNoteChange={setCompletionNote}
              onCompletionDocumentChange={setCompletionDocumentId}
              onLoadHistory={(task) => void loadTaskHistory(task)}
            />
          ) : active === "Риски и решения" ? (
            <GovernanceModule
              risks={risks}
              decisions={decisions}
              onUpdateRisk={(risk, status) => void updateRisk(risk, status)}
              onUpdateDecision={(decision, status) => void updateDecision(decision, status)}
            />
          ) : active === "Рабочий центр" || showSources ? (
            <>
              <section className="dashboard-hero">
                <div className="dashboard-hero-copy">
                  <span className="dashboard-kicker">ЦЕНТР УПРАВЛЕНИЯ · {new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}</span>
                  <h2>{projects.find((item) => item.id === projectId)?.name || "Текущий проект"}</h2>
                  <p>{dailyBriefing?.next_step || (summary?.attention ? `Сначала разберите ${summary.attention} пунктов, требующих вашего решения.` : "Проект под контролем. Новых критических событий нет.")}</p>
                  <div className="dashboard-hero-actions">
                    <button onClick={() => setActive("Сегодня")}><Route /> План на сегодня</button>
                    <button className="secondary" onClick={() => setActive("AI Secretary")}><Bot /> Спросить AI Secretary</button>
                  </div>
                </div>
                <div className={`dashboard-focus ${summary?.attention ? "needs-attention" : "clear"}`}>
                  <span>{summary?.attention ? "ГЛАВНЫЙ ФОКУС" : "СТАТУС ПРОЕКТА"}</span>
                  <strong>{summary?.attention || 0}</strong>
                  <p>{summary?.attention ? "пунктов ждут проверки" : "критичных пунктов нет"}</p>
                  <button onClick={() => setActive(summary?.attention ? "Риски и решения" : "Сегодня")}>
                    {summary?.attention ? "Разобрать сейчас" : "Открыть план"} <ArrowRight />
                  </button>
                </div>
              </section>
              <ManagementCenter projectId={projectId || null} enabled={ready} canManage={canManageSupply} />
              <div className="metrics dashboard-metrics">
                {metrics.map(([label, value, tone]) => (
                  <button
                    type="button"
                    className={String(tone)}
                    key={String(label)}
                    onClick={() => openMetric(String(label))}
                    aria-label={`Открыть раздел: ${label}`}
                  >
                    <span>{label}</span><strong>{value}</strong><small>Открыть раздел <ArrowRight /></small>
                  </button>
                ))}
              </div>
              <div className="dashboard-command-grid">
                <section className="card dashboard-attention-card">
                  <div className="card-head">
                    <div>
                      <span className="eyebrow">КОНТРОЛЬ И РЕШЕНИЯ</span>
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
                  <div className="dashboard-control-links">
                    <button onClick={() => { setTaskFilter("overdue"); setActive("Задачи"); }}><TimerReset /><span><strong>{summary?.overdue_tasks || 0}</strong> просроченных задач</span><ArrowRight /></button>
                    <button onClick={() => setActive("Риски и решения")}><AlertTriangle /><span><strong>{summary?.open_risks || 0}</strong> открытых рисков</span><ArrowRight /></button>
                    <button onClick={() => setActive("Риски и решения")}><ClipboardCheck /><span><strong>{summary?.pending_decisions || 0}</strong> решений ожидают</span><ArrowRight /></button>
                  </div>
                </section>
                <section className="card dashboard-actions-card">
                  <div className="card-head">
                    <div>
                      <span className="eyebrow">БЫСТРЫЙ СТАРТ</span>
                      <h2>Быстрые действия</h2>
                      <p>Без изменения оригиналов</p>
                    </div>
                  </div>
                  <div className="quick">
                    <button onClick={() => setActive("Письма")}><Mail /><span><strong>Разобрать письма</strong><small>Контекст и новые задачи</small></span><ArrowRight /></button>
                    <button onClick={() => setActive("Задачи")}><ListTodo /><span><strong>Открыть задачи</strong><small>Сроки и исполнители</small></span><ArrowRight /></button>
                    <button onClick={() => setActive("Запуск проекта")}><Route /><span><strong>Контур проекта</strong><small>Договор, ГПР и ДДС</small></span><ArrowRight /></button>
                    <button onClick={() => setActive("Интеграции")}><GitPullRequest /><span><strong>Источники данных</strong><small>Почта, Drive и Telegram</small></span><ArrowRight /></button>
                  </div>
                </section>
              </div>
              <div className="dashboard-lower-grid">
                <section className="card dashboard-inbox">
                  <div className="card-head">
                    <div>
                      <span className="eyebrow">КОММУНИКАЦИИ</span>
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
                <section className="card dashboard-ai-card">
                  <div className="dashboard-ai-icon"><Bot /></div>
                  <span className="eyebrow">AI SECRETARY</span>
                  <h2>Контекст проекта собран</h2>
                  <p>{dailyBriefing?.next_step || "Секретарь проверяет письма, документы, сроки и договорные обязательства."}</p>
                  <div className="dashboard-ai-facts">
                    <span><b>{inbox.filter((item) => item.drafts.some((draft) => draft.status !== "sent")).length}</b> черновиков</span>
                    <span><b>{summary?.unread_notifications || 0}</b> уведомлений</span>
                  </div>
                  <button onClick={() => setActive("AI Secretary")}>Открыть брифинг <ArrowRight /></button>
                </section>
              </div>
              <div className="grid dashboard-sources-grid">
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
                      <button onClick={() => void openSources()}>
                        Все источники
                      </button>
                      {latestSnapshot.provider !== "yandex_disk" && <a
                        className="source-link"
                        href={`https://drive.google.com/drive/folders/${latestSnapshot.source_external_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Открыть в Google Drive
                      </a>}
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
                      <button onClick={() => void openSources()}>
                        Выбрать рабочую папку
                      </button>
                    </div>
                  </section>
                )}
                {showSources && (
                  <section className="card span-3 drive-source-picker-modal" id="drive-source-picker">
                    <div className="card-head">
                      <div>
                        <h2>Папки для последовательного разбора</h2>
                        <p>
                          {sourceProvider === "yandex_disk" ? "Яндекс Диск" : sourceProvider ? "Google Drive" : "Хранилище проекта"}. Каждая папка получает отдельный виртуальный снимок;
                          файлы не перемещаются.
                        </p>
                      </div>
                      <div className="source-head-actions">
                        {["root", "disk:/"].includes(sourceFolderId) && (
                          <button
                            className="queue-all"
                            disabled={busyAll || picker.loading || !!busyFolder || !picker.context}
                            onClick={queueAllFolders}
                          >
                            {busyAll ? "Добавление…" : "Подготовить все папки"}
                          </button>
                        )}
                        <button onClick={picker.close}>
                          Закрыть
                        </button>
                      </div>
                    </div>
                    {picker.loading && <p role="status">Загружаю папки…</p>}
                    {picker.error && <div role="alert"><p>{picker.error}</p><button onClick={() => void picker.reopen()}>Переоткрыть выбор</button></div>}
                    {picker.context?.connection_id === null && <p>Это подключение пока не имеет идентификатора аккаунта. Проверка смены аккаунта ограничена; при переподключении переоткройте выбор.</p>}
                    {picker.selection?.project_id === projectId && <p role="status">
                      Выбрана: {picker.selection.source_folder} · снимок №{picker.selection.id}
                      {picker.selection.job_id != null && ` · задание №${picker.selection.job_id}`}
                      {` · статус снимка: ${picker.selection.status}`}
                      {picker.selection.analysis_status && ` · анализ: ${picker.selection.analysis_status}`}
                      {picker.selection.analysis_error && ` · ${picker.selection.analysis_error}`}
                    </p>}
                    {picker.context && sourceBreadcrumbs.length > 0 && <button
                      disabled={picker.loading || !!busyFolder || picker.needsReopen}
                      onClick={() => void queueFolder({ id: sourceFolderId, name: sourceBreadcrumbs.at(-1)!.name,
                        provider: sourceProvider, registered: false, is_primary: false, analyzed: false })}
                    >Выбрать текущую папку</button>}
                    <div className="source-breadcrumbs">
                      {sourceBreadcrumbs.map((crumb, index) => (
                        <button
                          key={crumb.id}
                          disabled={!!busyFolder || index === sourceBreadcrumbs.length - 1}
                          onClick={() => void openSources(crumb.id)}
                        >
                          {crumb.name}
                        </button>
                      ))}
                    </div>
                    <div className="source-list">
                      {folders.map((folder) => {
                        const session = processingQueue?.sessions.find((item) => item.id === folder.analysis_result?.organizer_session_id);
                        const isQueued = session?.status === "queued" || folder.job_status === "queued" || folder.job_status === "retrying";
                        const queuePosition = session?.queue_position;
                        const measuredProgress = session?.progress ?? folder.job_progress;
                        const activeProgress = measuredProgress == null ? null : Math.max(0, Math.min(100, measuredProgress));
                        const totalItems = session?.copy_item_count || session?.source_item_count || folder.item_count || 0;
                        const processedItems = session?.processed_item_count ?? null;
                        const remainingItems = processedItems === null || !totalItems ? null : Math.max(0, totalItems - processedItems);
                        const isProcessing = folder.snapshot_status === "building" || folder.analysis_status === "analyzing";
                        return (
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
                                            : sourceProvider === "yandex_disk" ? "Яндекс Диск" : "Google Drive"}
                            </p>
                            {isProcessing && <div className="source-progress" aria-label={activeProgress === null ? "Обработка: процент не предоставлен сервером" : `Прогресс анализа ${activeProgress}%`}>
                              <div className="source-progress-head"><strong>{isQueued ? "В очереди" : activeProgress === null ? "Обработка…" : `${activeProgress}%`}</strong><span>{isQueued && queuePosition ? `позиция ${queuePosition} · ` : ""}{processedItems === null ? "Количество обработанных объектов пока неизвестно" : `${processedItems.toLocaleString("ru-RU")} обработано`}{remainingItems !== null && ` · ${remainingItems.toLocaleString("ru-RU")} осталось`}</span></div>
                              {activeProgress !== null && <div className="source-progress-track"><i style={{ width: `${activeProgress}%` }} /></div>}
                              <small>{isQueued ? "Ожидает свободного обработчика" : session?.status || folder.job_status || "Создаётся снимок папки"}</small>
                            </div>}
                          </div>
                          <div className="source-row-actions">
                            <button disabled={!!busyFolder} onClick={() => void openSources(folder.id)}>
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
                              <>
                                <button
                                  disabled={!!busyFolder || picker.loading || picker.needsReopen}
                                  onClick={() => queueFolder(folder)}
                                >
                                  {busyFolder === folder.id ? "Подтверждаю…" : "Выбрать эту папку"}
                                </button>
                                <button
                                  className="secondary"
                                  disabled={!!busyFolder || picker.loading || picker.needsReopen}
                                  onClick={() => void refreshFolder(folder)}
                                >
                                  {busyFolder === folder.id ? "Обновляю снимок…" : "Обновить изменения с диска"}
                                </button>
                                <button
                                  className="analyze"
                                  disabled={
                                    busyFolder === folder.id ||
                                    folder.analysis_status === "analyzing"
                                  }
                                  onClick={() => prepareSourceChanges(folder)}
                                >
                                  {busyFolder === folder.id ||
                                  folder.analysis_status === "analyzing"
                                    ? "Анализ…"
                                    : folder.analyzed
                                      ? "Анализ без копии готов"
                                    : folder.analysis_status === "failed"
                                        ? "Повторить анализ без копии"
                                        : "Анализировать без копии"}
                                </button>
                                <button
                                  className="secondary"
                                  disabled={busyFolder === folder.id || folder.analysis_status === "analyzing"}
                                  onClick={() => analyzeFolder(folder)}
                                >
                                  Создать копию и стандартизировать
                                </button>
                              </>
                            ) : (
                              <button
                                disabled={
                                  folder.snapshot_status === "building" || !!busyFolder || picker.loading || picker.needsReopen
                                }
                                onClick={() => queueFolder(folder)}
                              >
                                {folder.snapshot_status === "building"
                                  ? "В очереди"
                                  : folder.snapshot_status === "failed"
                                    ? "Показать результат привязки"
                                    : folder.registered
                                      ? "Обновить копию и анализ"
                                      : "Подключить папку"}
                              </button>
                            )}
                          </div>
                        </article>
                        );
                      })}
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
          ) : null}
        </section>
      </main>
      <AndroidBottomNav active={active} onNavigate={(section) => { setActive(section); setMobile(false); }} onUpload={() => setMobileUploadOpen(true)} />
      <MobileDocumentUpload
        open={mobileUploadOpen}
        projectId={projectId}
        onClose={() => setMobileUploadOpen(false)}
        onComplete={(message) => { setNotice(message); void load(); setActive("Документы"); }}
      />
      {active === "Аналитика" && <AnalyticsModule analytics={analytics} collapsed={collapsed} onReload={() => void load()} />}
      {active === "Исполнение и финансы" && (
        <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
          <div className="module-page finance-page">
            <FinanceModule
              finance={finance}
              candidates={financeCandidates}
              contracts={contracts}
              selectedContractId={selectedFinanceContractId}
              onSelectContract={setSelectedFinanceContractId}
              onPrepare={prepareFinanceItem}
              onUseCandidate={(candidate) => void useFinanceCandidate(candidate)}
              onReload={() => void loadFinance()}
            />
            <FinanceOperations
              finance={finance}
              preview={financeStructuredPreview}
              selectedRows={financeStructuredRows}
              setSelectedRows={setFinanceStructuredRows}
              selectedContractId={selectedFinanceContractId}
              kind={financeKind}
              title={financeTitle}
              amount={financeAmount}
              date={financeDate}
              extra={financeExtra}
              sourceDocumentId={financeSourceDocumentId}
              scheduleItemId={financeScheduleItemId}
              budgetLineId={financeBudgetLineId}
              setKind={setFinanceKind}
              setTitle={setFinanceTitle}
              setAmount={setFinanceAmount}
              setDate={setFinanceDate}
              setExtra={setFinanceExtra}
              setScheduleItemId={setFinanceScheduleItemId}
              setBudgetLineId={setFinanceBudgetLineId}
              onClosePreview={() => {
                setFinanceStructuredPreview(null);
                setFinanceStructuredRows([]);
              }}
              onImport={() => void importStructuredFinance()}
              onAdd={() => void addFinanceItem()}
              onConfirm={(kind, id, status) =>
                void confirmFinance(kind, id, status)
              }
              onCloneBaseline={(id, version) => void cloneBaseline(id, version)}
              onUpdateSchedule={(id, currentProgress) => void updateScheduleActual(id, currentProgress)}
              onConfirmPayment={(id, amount, recordVersion) =>
                void confirmCashPayment(id, amount, recordVersion)
              }
              onCorrectPayment={(id, amount, date, recordVersion) =>
                void correctCashPayment(id, amount, date, recordVersion)
              }
            />
            <ForecastPanel
              report={forecast.report}
              state={forecast.state}
              error={forecast.error}
              acknowledgedForecastId={acknowledgedForecastId}
              onReload={() => void forecast.reload()}
              onAcknowledge={setAcknowledgedForecastId}
            />
            <SupplyCenter
              projectId={projectId || null}
              enabled={ready}
              canEdit={canEditSupply}
              canManage={canManageSupply}
            />
          </div>
        </section>
      )}
      {active === "Обязательства" && (
        <ObligationsModule
          collapsed={collapsed}
          obligations={obligations}
          onUpdate={(item, status) => void updateObligation(item, status)}
        />
      )}
      {active === "Совещания" && (
        <MeetingsModule
          collapsed={collapsed}
          meetings={meetings}
          title={newMeetingTitle}
          date={newMeetingDate}
          agenda={newMeetingAgenda}
          onTitleChange={setNewMeetingTitle}
          onDateChange={setNewMeetingDate}
          onAgendaChange={setNewMeetingAgenda}
          onCreate={() => void createMeeting()}
          onRecordMinutes={(meeting) => void recordMinutes(meeting)}
        />
      )}
      {active === "Уведомления" && (
        <NotificationsModule
          collapsed={collapsed}
          notifications={notifications}
          onRefresh={() => void refreshNotifications()}
          onMarkRead={(item) => void markNotification(item)}
        />
      )}
      {active === "Договоры" && (
        <ContractsModule
          collapsed={collapsed}
          number={newContractNumber}
          title={newContractTitle}
          counterparty={newCounterparty}
          kind={newContractKind}
          parentContractId={newParentContractId}
          amount={newContractAmount}
          advanceAmount={newAdvanceAmount}
          retentionPercent={newRetentionPercent}
          signedAt={newContractSignedAt}
          contracts={contracts}
          onNumberChange={setNewContractNumber}
          onTitleChange={setNewContractTitle}
          onCounterpartyChange={setNewCounterparty}
          onKindChange={setNewContractKind}
          onParentContractIdChange={setNewParentContractId}
          onAmountChange={setNewContractAmount}
          onAdvanceAmountChange={setNewAdvanceAmount}
          onRetentionPercentChange={setNewRetentionPercent}
          onSignedAtChange={setNewContractSignedAt}
          onCreate={() => void createContract()}
        >
            <section className="contract-list">
              <ContractBulkImportWizard
                documents={documentRows}
                contracts={contracts}
                onDiscover={discoverBulkContracts}
                onImport={importBulkContracts}
                incomingProposals={droppedContractProposals}
                onIncomingConsumed={() => setDroppedContractProposals([])}
              />
              <ContractScheme
                projectId={projectId}
                contracts={contracts}
                onConnect={(parentId, childId) => {
                  const child = contracts.find((item) => item.id === childId);
                  if (child) void linkContractParent(child.id, child.contract_kind || "customer", parentId);
                }}
                onOpenDocument={(documentId) => {
                  const document = documentRows.find((item) => item.id === documentId);
                  if (document) { void openDocument(document); setActive("Документы"); }
                }}
                onDelete={(contract) => void deleteContract(contract as ContractRow)}
                onArchive={(contract) => void archiveContract(contract as ContractRow)}
                onDropDocuments={(documentIds, parentId) => void prepareDroppedContracts(documentIds, parentId)}
                onDropFiles={(files, parentId) => void uploadDroppedContracts(files, parentId)}
                onDropApplications={(files, contractId) => void uploadContractApplications(files, contractId)}
                onDropFinance={(files, contractId, kind) => void uploadContractFinance(files, contractId, kind)}
                operationStatus={contractDropStatus}
              />
              <header className="contract-project-root">
                <FolderKanban />
                <div><span>ПРОЕКТ · КОРЕНЬ ДЕРЕВА</span><h2>{projects.find((project) => project.id === projectId)?.name || "Выбранный проект"}</h2><p>Все договоры проекта собраны в единую цепочку подчинённости</p></div>
                <strong className="contract-project-count">{contracts.length}<small>договоров</small></strong>
              </header>
              {buildContractTree(contracts, query).map(({ item, depth, hasChildren, parentMissing }) => (
                  <article className="card contract-card contract-tree-node" data-depth={depth} data-kind={item.contract_kind || "customer"} style={{ "--contract-depth": depth } as React.CSSProperties} key={item.id}>
                    <div className="contract-number">{item.number}</div>
                    <div>
                      <div className="contract-tree-position">
                        <span className="contract-tree-level">{depth === 0 ? "Головной договор" : `Уровень ${depth + 1}`}</span>
                        {hasChildren && <b>Ветвь продолжается</b>}
                        {parentMissing && <b className="warning">Вышестоящий договор не найден</b>}
                      </div>
                      <span className={`contract-status ${item.status}`}>
                        {item.contract_kind === "prime_reference" ? "ГЕНПОДРЯД · КОНТЕКСТ" : item.contract_kind === "revenue_subcontract" ? "НАШ СУБПОДРЯД · ДОХОД" : item.contract_kind === "downstream_subcontract" ? "НАШ ИСПОЛНИТЕЛЬ · РАСХОД" : item.contract_kind === "supply" ? "ПОСТАВКА · РАСХОД" : "ПРЯМОЙ ДОГОВОР · ДОХОД"} · {item.status}
                      </span>
                      <span className={`contract-source-badge ${item.source_document_id ? "linked" : "missing"}`}>
                        {item.source_document_id ? "✓ Договор привязан к документу" : "! Документ договора не привязан"}
                      </span>
                      <small>Версия карточки: {item.record_version} · снимков истории: {item.version_history?.length || 0}</small>
                      <h2>{item.title}</h2>
                      <p>
                        {item.counterparty || "Контрагент не указан"}
                        {item.signed_at
                          ? ` · от ${new Date(item.signed_at).toLocaleDateString("ru-RU")}`
                          : ""}
                      </p>
                      {item.notes && <small>{item.notes}</small>}
                      {item.parent_contract_id && <small>
                        Связан с договором {contracts.find((parent) => parent.id === item.parent_contract_id)?.number || `№${item.parent_contract_id}`}
                      </small>}
                      {(() => {
                        const draft = contractStructureDrafts[item.id] || { kind: item.contract_kind || "customer", parentId: item.parent_contract_id || 0 };
                        const needsParent = !["prime_reference", "customer"].includes(draft.kind);
                        return <div className="contract-parent-link">
                          <span>Место в дереве</span>
                          <select value={draft.kind} onChange={(event) => setContractStructureDrafts((current) => ({ ...current, [item.id]: { kind: event.target.value, parentId: 0 } }))}>
                            <option value="prime_reference">Генподрядный договор — корень</option>
                            <option value="customer">Прямой договор — корень</option>
                            <option value="revenue_subcontract">Наш договор под генподрядным</option>
                            <option value="downstream_subcontract">Субподрядчик / субсубподрядчик</option>
                            <option value="supply">Поставщик</option>
                          </select>
                          {needsParent && <select value={draft.parentId} onChange={(event) => setContractStructureDrafts((current) => ({ ...current, [item.id]: { ...draft, parentId: Number(event.target.value) } }))}>
                            <option value={0}>Выберите непосредственный вышестоящий договор</option>
                            {contracts.filter((candidate) => {
                              if (candidate.id === item.id) return false;
                              if (draft.kind === "revenue_subcontract") return ["prime_reference", "customer"].includes(candidate.contract_kind || "customer");
                              return ["prime_reference", "customer", "revenue_subcontract", "downstream_subcontract"].includes(candidate.contract_kind || "customer");
                            }).map((candidate) => <option value={candidate.id} key={candidate.id}>{candidate.number} — {candidate.counterparty || candidate.title}</option>)}
                          </select>}
                          <button className="secondary" disabled={needsParent && !draft.parentId} onClick={() => void linkContractParent(item.id, draft.kind, draft.parentId)}>Сохранить связь</button>
                        </div>;
                      })()}
                      {(item.amount || item.advance_amount || item.retention_percent || item.warranty_until) && <small>
                        {item.amount ? `Сумма ${money(item.amount)}` : "Сумма не указана"}
                        {item.advance_amount ? ` · аванс ${money(item.advance_amount)}` : ""}
                        {item.retention_percent ? ` · удержание ${item.retention_percent}%` : ""}
                        {item.warranty_until ? ` · гарантия до ${new Date(item.warranty_until).toLocaleDateString("ru-RU")}` : ""}
                      </small>}
                      {contractEditDrafts[item.id] ? (() => {
                        const draft = contractEditDrafts[item.id];
                        const change = (field: keyof ContractEditDraft, value: string) => setContractEditDrafts((current) => ({
                          ...current, [item.id]: { ...current[item.id], [field]: value },
                        }));
                        return <div className="contract-edit-form">
                          <strong>Редактирование договора</strong>
                          <input value={draft.number} onChange={(event) => change("number", event.target.value)} placeholder="Номер договора" />
                          <input value={draft.title} onChange={(event) => change("title", event.target.value)} placeholder="Название" />
                          <input value={draft.counterparty} onChange={(event) => change("counterparty", event.target.value)} placeholder="Контрагент" />
                          <input type="number" min="0" step="0.01" value={draft.amount} onChange={(event) => change("amount", event.target.value)} placeholder="Сумма договора, ₽" />
                          <input type="number" min="0" step="0.01" value={draft.advanceAmount} onChange={(event) => change("advanceAmount", event.target.value)} placeholder="Аванс, ₽" />
                          <input type="number" min="0" max="100" step="0.01" value={draft.retentionPercent} onChange={(event) => change("retentionPercent", event.target.value)} placeholder="Удержание, %" />
                          <label>Дата подписания<input type="date" value={draft.signedAt} onChange={(event) => change("signedAt", event.target.value)} /></label>
                          <select value={draft.status} onChange={(event) => change("status", event.target.value)}>
                            <option value="draft">Черновик</option><option value="active">Действует</option>
                            <option value="completed">Завершён</option><option value="terminated">Расторгнут</option><option value="archived">В архиве</option>
                          </select>
                          <div className="contract-edit-actions">
                            <button className="secondary" onClick={() => setContractEditDrafts((current) => { const next = { ...current }; delete next[item.id]; return next; })}>Отмена</button>
                            <button disabled={!draft.number.trim() || !draft.title.trim()} onClick={() => void saveContractEdit(item.id)}>Сохранить изменения</button>
                          </div>
                        </div>;
                      })() : <div className="contract-record-actions">
                        <button className="secondary" onClick={() => beginContractEdit(item)}>Редактировать</button>
                        {item.status !== "archived" && <button className="secondary" onClick={() => void archiveContract(item)}>Архивировать</button>}
                        <button className="danger" onClick={() => void deleteContract(item)}><Trash2 /> Удалить договор</button>
                      </div>}
                    </div>
                    <div className="contract-links">
                      <ContractDocumentPicker
                        contractId={item.id}
                        sourceDocumentId={item.source_document_id}
                        open={Boolean(contractCatalogOpen[item.id])}
                        busy={contractCandidateBusy === item.id}
                        tab={contractDocumentTabs[item.id] || "recommended"}
                        query={contractDocumentQueries[item.id] || ""}
                        documents={documentRows}
                        candidates={contractSourceCandidates[item.id] || []}
                        onOpen={() => {
                          setContractCatalogOpen((current) => ({ ...current, [item.id]: true }));
                          setContractDocumentTabs((current) => ({ ...current, [item.id]: "server" }));
                        }}
                        onClose={() => setContractCatalogOpen((current) => ({ ...current, [item.id]: false }))}
                        onSuggest={() => void suggestContractDocuments(item.id)}
                        onTabChange={(tab) => setContractDocumentTabs((current) => ({ ...current, [item.id]: tab }))}
                        onQueryChange={(value) => setContractDocumentQueries((current) => ({ ...current, [item.id]: value }))}
                        onLink={async (documentId) => {
                          if (await linkContractDocument(item.id, documentId)) {
                            setContractCatalogOpen((current) => ({ ...current, [item.id]: false }));
                            setNotice("Договор привязан. Теперь нажмите «Проанализировать договор».");
                          }
                        }}
                      />
                      <button
                        className="secondary"
                        onClick={() => analyzeContract(item.id)}
                      >
                        2. Автоматически прикрепить, прочитать и проверить условия
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
                      {contractFinancialChecks[item.id] && (
                        <div className={`contract-analysis-status ${contractFinancialChecks[item.id].mismatches.length ? "warning" : ""}`} role="status">
                          <span>Источник <b>{contractFinancialChecks[item.id].sourceName || "договор"}</b></span>
                          <span>Сумма <b>{item.amount ? money(item.amount) : "не найдена"}</b></span>
                          <span>Аванс <b>{item.advance_amount ? money(item.advance_amount) : "не найден"}</b></span>
                          <span>Удержание <b>{item.retention_percent ? `${item.retention_percent}%` : "не найдено"}</b></span>
                          <span>Сверка <b>{contractFinancialChecks[item.id].mismatches.length ? `расхождений: ${contractFinancialChecks[item.id].mismatches.length}` : "совпадает"}</b></span>
                          {contractFinancialChecks[item.id].mismatches.map((mismatch) => <small key={mismatch.field}>
                            {mismatch.label}: в карточке {mismatch.current}, в договоре {mismatch.extracted}. {mismatch.evidence || "Проверьте исходный пункт договора."}
                          </small>)}
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
        </ContractsModule>
      )}
      {active === "Предложения" && (
        <ProposalsModule
          projectId={projectId}
          collapsed={collapsed}
          proposals={proposals}
          busyProposal={busyProposal}
          targetFolders={targetFolders}
          onOpenDocuments={() => setActive("Документы")}
          onApproveSafe={(proposal) => void approveSafe(proposal)}
          onApply={(proposal) => void applyProposal(proposal)}
          onStandardize={(proposal) => void standardizeProposal(proposal)}
          onRollback={(proposal) => void rollbackProposal(proposal)}
          onDecision={(action, decision) => void editProposalAction(action, decision)}
          onSave={(proposalId, actionId) => void saveProposalAction(proposalId, actionId)}
          onEdit={(proposalId, actionId, patch) => setProposals((rows) => rows.map((row) =>
            row.id === proposalId
              ? { ...row, actions: row.actions.map((action) => action.id === actionId ? { ...action, ...patch } : action) }
              : row
          ))}
          onApplySource={(proposal, action) => void applyOneToSource(proposal, action)}
          onApplySourceBulk={(proposal) => void applyApprovedToSource(proposal)}
        />
      )}
      {active === "Интеграции" && (
        <IntegrationsModule
          collapsed={collapsed}
          items={integrationItems}
          systemState={systemState}
          gmailSyncing={gmailSyncing}
          gmailSyncStatus={gmailSyncStatus}
          onSyncGmail={() => void syncGmail()}
          onSelectFolder={(provider) => void openProviderSources(provider)}
          onConnectProvider={(provider) => void connectStorageProvider(provider)}
          onLocalUpload={() => { setActive("Рабочий центр"); setNotice("Нажмите «Загрузить рабочую папку» в рабочем центре"); }}
          onOpenAIPolicy={() => setActive("Настройки")}
          onOpenGmailResults={openGmailResults}
          onReload={() => void loadIntegrations()}
        />
      )}
      {active === "Журнал" && (
        <AuditModule
          collapsed={collapsed}
          logs={auditLogs}
          query={query}
          onReload={() => void load()}
        />
      )}
      {active === "Настройки" && (
        <SettingsModule
          collapsed={collapsed}
          currentUser={currentUser}
          activeProjectName={projects.find((item) => item.id === projectId)?.name}
          members={members}
          aiPolicy={aiPolicy}
          processingQueue={processingQueue}
          onPolicyChange={setAiPolicy}
          onSavePolicy={() => void saveAIPolicy()}
          onRetrySnapshot={(id) => void retrySnapshot(id)}
          onRetrySession={(id) => void retryOrganizerSession(id)}
          onPasswordChanged={() => window.location.reload()}
        />
      )}
      {(active === "AI Secretary" || active === "Письма") && (
        <InboxModule
          collapsed={collapsed}
          mode={active === "Письма" ? "mail" : "secretary"}
          attentionCount={inbox.filter((item) => !item.context_confirmed || item.tasks.some((task) => task.external_action_status === "proposed")).length}
          syncing={gmailSyncing}
          onSync={() => void syncGmail()}
        >
            {active === "Письма" && <div className="mail-view-tabs">
              <button className={mailView === "inbox" ? "selected" : ""} onClick={() => setMailView("inbox")}>Входящие</button>
              <button className={mailView === "companies" ? "selected" : ""} onClick={() => setMailView("companies")}>Компании и контакты <b>{projectContacts.length}</b></button>
            </div>}
            {active === "Письма" && mailView === "companies" && <ContactsModule
              projectId={projectId}
              contacts={projectContacts}
              contracts={contracts}
              drafts={drafts}
              reload={() => load()}
              onNotice={setNotice}
              onError={setError}
              onUpdateDraft={(draft, status) => void updateDraft(draft, status)}
              onSendDraft={(draft) => void sendGmailDraft(draft)}
            />}
            {active === "AI Secretary" && (
              <DailyBriefingPanel briefing={dailyBriefing} onOpenSection={setActive} />
            )}
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
            <section className="inbox-list" style={{ display: active === "Письма" && mailView !== "inbox" ? "none" : undefined }}>
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
                        <span className={`draft-status ${messageWorkflowClass(message.workflow_state || (message.context_confirmed ? "ready" : "needs_context_confirmation"))}`}>
                          {messageWorkflowLabel(message.workflow_state || (message.context_confirmed ? "ready" : "needs_context_confirmation"))}
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
                        {message.workflow_reason && <small>{message.workflow_reason}</small>}
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
                    <EvidencePanel active={expanded} evidenceRefs={message.evidence_refs || []} />
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
                    {message.completion_suggestions?.length > 0 && (
                      <div className="inbox-proposals">
                        <h3>Исходящий ответ — возможное выполнение задач</h3>
                        {message.completion_suggestions.map((suggestion) => (
                          <div className="inbox-task" key={suggestion.id}>
                            <div>
                              <strong>{suggestion.task_title}</strong>
                              <p>{suggestion.evidence} · уверенность {Math.round(suggestion.confidence * 100)}%</p>
                            </div>
                            <span>{suggestion.status === "proposed" ? "Ожидает проверки" : suggestion.status === "confirmed" ? "Выполнение подтверждено" : "Отклонено"}</span>
                            {suggestion.status === "proposed" && <div className="draft-actions">
                              <button onClick={() => reviewOutgoingCompletion(message, suggestion.id, "rejected")}>Не выполнена</button>
                              <button className="approve" onClick={() => reviewOutgoingCompletion(message, suggestion.id, "confirmed")}>Подтвердить выполнение</button>
                            </div>}
                          </div>
                        ))}
                      </div>
                    )}
                    {message.drafts.map((draft) => (
                      <div className="inbox-draft" key={draft.id}>
                        {draft.status === "draft" ? (
                          <input
                            aria-label="Тема ответа"
                            value={draft.subject}
                            onChange={(e) => setInbox((rows) => rows.map((row) => row.id === message.id
                              ? { ...row, drafts: row.drafts.map((item) => item.id === draft.id
                                ? { ...item, subject: e.target.value }
                                : item) }
                              : row))}
                          />
                        ) : <h3>{draft.subject}</h3>}
                        {message.source_type === "email" && (
                          <input
                            type="email"
                            aria-label="Получатель ответа"
                            value={draft.recipient_to || message.source_sender || ""}
                            disabled={draft.status !== "draft"}
                            onChange={(e) => setInbox((rows) => rows.map((row) => row.id === message.id
                              ? { ...row, drafts: row.drafts.map((item) => item.id === draft.id
                                ? { ...item, recipient_to: e.target.value }
                                : item) }
                              : row))}
                          />
                        )}
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
                        {draft.status === "sent" && (
                          <EmailCompensationCard
                            offer={draft.email_compensation || undefined}
                            onPropose={(offer) => proposeEmailCompensation(draft, offer)}
                          />
                        )}
                        {draft.status === "draft" && !draft.is_corrective_follow_up && (
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
        </InboxModule>
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
                      {copyCleanupResults[item.id] && (
                        <div className="project-cleanup-result" role="status">
                          <ShieldCheck />
                          <div>
                            <strong>{copyCleanupResults[item.id].message}</strong>
                            <small>Следующий шаг — архивировать карточку проекта.</small>
                          </div>
                        </div>
                      )}
                      <div className="project-actions">
                        <button onClick={() => activateProject(item.id)}>
                          {item.id === projectId ? "Открыть рабочий центр" : "Переключиться на проект"}
                        </button>
                        <button className="secondary" onClick={() => cleanupProjectCopies(item)} title="Переместить созданные системой копии в корзину Google Drive">
                          <Trash2 /> Очистить копии
                        </button>
                        <button className="danger" onClick={() => archiveProject(item)} title="Скрыть проект без удаления данных">
                          <Archive /> {copyCleanupResults[item.id] ? "Архивировать проект" : "Архивировать"}
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
        <DocumentsModule collapsed={collapsed} knowledgeMode={active === "Центр знаний"} documents={visibleDocuments} selected={selectedDocument} onSelect={(item) => void openDocument(item)} projectId={projectId} onOcrComplete={() => { setNotice("Повторное OCR завершено. Реестр и связи обновлены."); void load(); }} />
      )}
      <ContextualAssistant section={active} onAsk={openContextualAssistant} />
    </div>
  );
}
