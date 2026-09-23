import { test as base, expect, type Page, type Request, type Route } from "@playwright/test";

export type Provider = "google_drive" | "yandex_disk";
type Reply = { status?: number; body: unknown };
export const origin = "http://127.0.0.1:4179";
export const specialPath = "disk:/Заказчик/Проект #1/Этап ? 50%";
const projects = [{ id: 1, name: "Persistent Project" }, { id: 2, name: "Новый проект" }];
export const paths = {
  google_drive: ["root", "opaque-client-A", "opaque-project-B", "opaque-stage-C"],
  yandex_disk: ["disk:/", "disk:/Заказчик", "disk:/Заказчик/Проект #1", specialPath],
};
const names = ["Мой диск", "Заказчик", "Проект #1", "Этап ? 50%"];
export const binding = (provider: Provider, project_id = 2) => ({ project_id, provider, connection_id: `synthetic-${provider}-${project_id}`, connection_row_id: project_id + 70 });
export const folder = (provider: Provider, id: string, name: string) => ({ id, name, provider, modifiedTime: null, registered: false,
  is_primary: false, snapshot_id: null as number | null, snapshot_status: null as string | null, item_count: null as number | null,
  analyzed: false, analysis_status: null as string | null, analysis_result: null as Record<string, unknown> | null, analysis_error: null });
export function discovery(provider: Provider, id = paths[provider][1], projectId = 2) {
  const depth = paths[provider].indexOf(id);
  if (depth < 0) throw new Error(`Unknown synthetic folder: ${id}`);
  return { ...binding(provider, projectId), folder_id: id,
    breadcrumbs: paths[provider].slice(0, depth + 1).map((value, index) => ({ id: value, name: names[index] })),
    folders: depth < 3 ? [folder(provider, paths[provider][depth + 1], names[depth + 1])] : [] };
}
const emptyQueue = () => ({ summary: { active: 0, failed: 0, dead_letter: 0 }, snapshots: [], sessions: [] });

/** Explicitly allowlisted synthetic HTTP API. No catch-all 200 and no backend proxy. */
export class StorageApi {
  provider: Provider = "google_drive";
  requests: { method: string; path: string; body: string | null }[] = [];
  unexpected: string[] = [];
  errors: string[] = [];
  projectRows = [...projects];
  inboxByProject = new Map<number, Record<string, unknown>[]>();
  evidenceReplies = new Map<string, Reply>();
  aiPolicies = new Map<number, Record<string, unknown>>();
  meetings: Record<string, unknown>[] = [];
  attentionItems: Record<string, unknown>[] = [];
  searchItems: Record<string, unknown>[] = [{
    entity_type: "task", entity_id: 901, name: "Подготовить акт", date: "2026-09-25",
    project_id: 2, contract_id: 41, counterparty: "ООО Фасад", status: "open",
    navigation: { section: "tasks", project_id: 2, entity_type: "task", entity_id: 901 },
  }];
  savedSearchViews: Record<string, unknown>[] = [{
    id: 601, project_id: 2, owner_user_id: 900, name: "Срочные задачи",
    filters: { q: "акт", types: ["task"] }, record_version: 1,
    created_at: "2026-09-20T10:00:00Z", updated_at: "2026-09-20T10:00:00Z",
  }];
  resources: Record<string, unknown>[] = [];
  meetingProposals = new Map<number, Record<string, unknown>[]>();
  meetingBindingReply?: Reply;
  meetingSource = {
    document_id: 731, display_name: "Протокол совещания.pdf", media_type: "application/pdf",
    source_id: "11111111-1111-4111-8111-111111111111",
    source_version_id: "22222222-2222-4222-8222-222222222222",
    evidence_id: "33333333-3333-4333-8333-333333333333",
    materialization_id: "44444444-4444-4444-8444-444444444444",
    observed_at: "2026-09-20T10:00:00Z",
  };
  attachmentImportReply: Reply = { body: {
    staging_id: "synthetic-gmail-staging", job_id: 72, status: "queued", already_queued: false,
  } };
  localUploadReplies: Reply[] = [{ body: {
    status: "queued", processed: 0, tasks: 0, risks: 0, skipped: [], drafts: 0, documents: [],
    jobs: [{ job_id: 73, staging_id: "synthetic-local-staging", status: "queued" }],
  } }];
  roots = new Map<number, string>();
  snapshots: Record<string, unknown>[] = [];
  queue: { summary: { active: number; failed: number; dead_letter: number }; snapshots: unknown[]; sessions: unknown[] } = emptyQueue();
  discoveryReply?: (url: URL) => Reply;
  confirmReply?: Reply;
  analyzeReply: Reply = { body: { snapshot_id: 31, status: "analyzing", already_queued: true } };
  standardizeReply: Reply = { body: { snapshot_id: 31, session_id: 42, status: "retrying", already_queued: true } };
  private holds: { match: (url: URL) => boolean; arrived: (request: Request) => void; reply: Promise<Reply> }[] = [];

  hold(match: (url: URL) => boolean) {
    let arrived!: (request: Request) => void;
    let release!: (reply: Reply) => void;
    const request = new Promise<Request>(resolve => { arrived = resolve; });
    const reply = new Promise<Reply>(resolve => { release = resolve; });
    this.holds.push({ match, arrived, reply });
    return { request, release };
  }
  count(fragment: string) { return this.requests.filter(request => request.path.includes(fragment)).length; }
  confirm(id: string, projectId = 2, status = "building") {
    return { ...binding(this.provider, projectId), id: 31, job_id: 42, folder_id: id,
      source_folder: names[paths[this.provider].indexOf(id)], status, already_queued: false };
  }
  async route(route: Route) {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.origin === origin && method === "GET" && request.resourceType() !== "fetch"
      && (url.pathname === "/new/" || /^\/new\/assets\/[\w.-]+\.(js|css)$/.test(url.pathname)
        || ["/new/pu-icon.svg", "/new/manifest.webmanifest"].includes(url.pathname))) {
      await route.continue(); return;
    }
    this.requests.push({ method, path: url.pathname + url.search, body: request.postData() });
    if (url.origin !== origin) return this.block(route, `External ${method} ${url.origin}${url.pathname}`);
    const holdIndex = this.holds.findIndex(item => item.match(url));
    if (holdIndex >= 0) {
      const [hold] = this.holds.splice(holdIndex, 1);
      hold.arrived(request);
      return this.fulfill(route, await hold.reply);
    }
    const path = url.pathname;
    const projectId = Number(path.match(/^\/projects\/(\d+)/)?.[1] || url.searchParams.get("project_id") || 2);
    if (method === "GET" && path === "/project-search") {
      const cursor = url.searchParams.get("cursor");
      const rows = this.searchItems.filter(row => Number(row.project_id) === projectId);
      return this.fulfill(route, { body: {
        items: cursor ? rows.slice(1) : rows.slice(0, 1),
        next_cursor: !cursor && rows.length > 1 ? "synthetic-page-2" : null,
        limit: Number(url.searchParams.get("limit") || 50), scan_truncated: false,
        scan_cap_per_type: 1000, external_actions_created: false,
      } });
    }
    if (method === "GET" && path === "/saved-search-views") {
      return this.fulfill(route, { body: { views: this.savedSearchViews.filter(row => Number(row.project_id) === projectId) } });
    }
    if (method === "POST" && path === "/saved-search-views") {
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      const created = { ...payload, id: 602, owner_user_id: 900, record_version: 1,
        created_at: "2026-09-20T10:00:00Z", updated_at: "2026-09-20T10:00:00Z" };
      this.savedSearchViews.push(created);
      return this.fulfill(route, { status: 201, body: created });
    }
    const savedViewMatch = path.match(/^\/saved-search-views\/(\d+)$/);
    if (savedViewMatch && method === "PATCH") {
      const id = Number(savedViewMatch[1]);
      const row = this.savedSearchViews.find(item => Number(item.id) === id);
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      if (!row) return this.fulfill(route, { status: 404, body: { detail: "Saved view not found" } });
      if (Number(payload.expected_record_version) !== Number(row.record_version)) return this.fulfill(route, { status: 409, body: { detail: "record_version_conflict" } });
      Object.assign(row, { ...payload, record_version: Number(row.record_version) + 1 });
      delete row.expected_record_version;
      return this.fulfill(route, { body: row });
    }
    if (savedViewMatch && method === "DELETE") {
      const id = Number(savedViewMatch[1]);
      const row = this.savedSearchViews.find(item => Number(item.id) === id);
      if (!row) return this.fulfill(route, { status: 404, body: { detail: "Saved view not found" } });
      const expected = Number(url.searchParams.get("expected_record_version"));
      if (expected !== Number(row.record_version)) return this.fulfill(route, { status: 409, body: { detail: "record_version_conflict" } });
      this.savedSearchViews = this.savedSearchViews.filter(item => Number(item.id) !== id);
      return this.fulfill(route, { body: { id, record_version: expected + 1, deleted: true } });
    }
    const evidenceMatch = path.match(/^\/api\/v54\/evidence\/([^/]+)\/fragment$/);
    if (method === "GET" && evidenceMatch) {
      const evidenceId = decodeURIComponent(evidenceMatch[1]);
      return this.fulfill(route, this.evidenceReplies.get(evidenceId) || {
        status: 404,
        body: { detail: "Synthetic evidence is unavailable" },
      });
    }
    const attachmentMatch = path.match(/^\/ai-secretary\/inbox\/(\d+)\/attachments\/(\d+)\/import$/);
    if (method === "POST" && attachmentMatch) {
      const messageId = Number(attachmentMatch[1]);
      const attachmentIndex = Number(attachmentMatch[2]);
      const rows = this.inboxByProject.get(projectId) || [];
      const message = rows.find(row => row.id === messageId);
      const attachments = message?.attachments;
      if (Array.isArray(attachments) && attachments[attachmentIndex] && typeof attachments[attachmentIndex] === "object") {
        attachments[attachmentIndex] = { ...attachments[attachmentIndex], imported: true, document_id: 501 };
      }
      return this.fulfill(route, this.attachmentImportReply);
    }
    if (method === "POST" && path === "/local-upload/analyze") {
      return this.fulfill(route, this.localUploadReplies.shift() || {
        status: 503,
        body: { detail: "Synthetic local upload reply was not configured" },
      });
    }
    if (method === "POST" && path === "/management/meetings") {
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      const start = new Date(String(payload.scheduled_at));
      const duration = Number(payload.duration_minutes);
      const userIds = (payload.participant_user_ids || []) as number[];
      const resourceIds = (payload.resource_ids || []) as number[];
      const conflicts = this.meetings.filter(row => {
        const shared = ((row.participant_user_ids || []) as number[]).some(id => userIds.includes(id));
        const sharedResource = ((row.resource_ids || []) as number[]).some(id => resourceIds.includes(id));
        if ((!shared && !sharedResource) || !row.scheduled_at || !row.duration_minutes || row.status === "cancelled") return false;
        const otherStart = new Date(String(row.scheduled_at));
        const otherEnd = new Date(otherStart.getTime() + Number(row.duration_minutes) * 60_000);
        const end = new Date(start.getTime() + duration * 60_000);
        return start < otherEnd && otherStart < end;
      });
      const participants = userIds.map(id => ({ kind: "user", id, name: "Synthetic Operator", email: "operator@example.invalid" }));
      const resourceRefs = this.resources.filter(row => resourceIds.includes(Number(row.id)));
      const created = {
        ...payload, id: this.meetings.length + 1, record_version: 1, status: "planned",
        can_edit: true, can_manage: true,
        participant_refs: participants, participant_contact_ids: payload.participant_contact_ids || [],
        resource_ids: resourceIds, resource_refs: resourceRefs,
        has_conflicts: conflicts.length > 0, conflict_count: conflicts.length,
        conflicts: conflicts.map(row => ({ meeting_id: row.id, project_id: row.project_id,
          title: row.title, overlap_from: payload.scheduled_at, overlap_to: row.scheduled_at,
          participants, resources: this.resources.filter(resource =>
            resourceIds.includes(Number(resource.id)) && ((row.resource_ids || []) as number[]).includes(Number(resource.id))),
          redacted: false })),
        has_resource_warnings: false, resource_warnings: [],
      };
      this.meetings.unshift(created);
      return this.fulfill(route, { body: created });
    }
    if (method === "POST" && path === "/management/resources") {
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      const created = {
        ...payload, id: this.resources.length + 501, record_version: 1,
        organization_id: 901, managing_project_id: Number(payload.project_id), active: true,
      };
      this.resources.unshift(created);
      return this.fulfill(route, { body: created });
    }
    const resourceMatch = path.match(/^\/management\/resources\/(\d+)$/);
    if (method === "PATCH" && resourceMatch) {
      const id = Number(resourceMatch[1]);
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      const resource = this.resources.find(row => Number(row.id) === id);
      if (!resource) return this.fulfill(route, { status: 404, body: { detail: "Resource not found" } });
      Object.assign(resource, payload, { record_version: Number(resource.record_version) + 1 });
      return this.fulfill(route, { body: resource });
    }
    const meetingMatch = path.match(/^\/management\/meetings\/(\d+)$/);
    if (method === "PATCH" && meetingMatch) {
      const id = Number(meetingMatch[1]);
      const row = this.meetings.find((item) => item.id === id);
      if (!row) return this.fulfill(route, { status: 404, body: { detail: "Meeting not found" } });
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      if (payload.expected_record_version !== row.record_version) {
        return this.fulfill(route, { status: 409, body: { detail: "record_version_conflict" } });
      }
      row.minutes = payload.minutes; row.status = payload.status;
      row.record_version = Number(row.record_version) + 1;
      return this.fulfill(route, { body: {
        id, status: row.status, record_version: row.record_version,
        tasks: 0, risks: 0, decisions: 0, proposals: 0,
        proposal_state: "source_binding_required",
      } });
    }
    const sourceCandidateMatch = path.match(/^\/management\/meetings\/(\d+)\/source-candidates$/);
    if (method === "GET" && sourceCandidateMatch) {
      return this.fulfill(route, { body: { candidates: [this.meetingSource], count: 1 } });
    }
    const proposalListMatch = path.match(/^\/management\/meetings\/(\d+)\/proposals$/);
    if (method === "GET" && proposalListMatch) {
      const id = Number(proposalListMatch[1]);
      const proposals = this.meetingProposals.get(id) || [];
      return this.fulfill(route, { body: {
        proposals, count: proposals.length,
        source_binding: proposals.length ? this.meetingSource : null,
      } });
    }
    const sourceBindingMatch = path.match(/^\/management\/meetings\/(\d+)\/source-binding$/);
    if (method === "POST" && sourceBindingMatch) {
      if (this.meetingBindingReply) {
        const reply = this.meetingBindingReply; this.meetingBindingReply = undefined;
        return this.fulfill(route, reply);
      }
      const id = Number(sourceBindingMatch[1]);
      const row = this.meetings.find((item) => item.id === id);
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      if (!row || payload.expected_record_version !== row.record_version) {
        return this.fulfill(route, { status: 409, body: { detail: { code: "record_version_conflict" } } });
      }
      row.record_version = Number(row.record_version) + 1;
      const proposals = [{
        id: 501, record_version: 1, project_id: row.project_id, meeting_id: id,
        binding_id: "binding-501", proposal_type: "task",
        payload: { title: "Подготовить акт", excerpt: "Подготовить акт до 25.09.2026",
          due_date_evidence_quote: "до 25.09.2026", confidence: 0.94 },
        status: "proposed", target_entity_type: null, target_entity_id: null,
      }];
      this.meetingProposals.set(id, proposals);
      return this.fulfill(route, { body: {
        meeting_id: id, meeting_record_version: row.record_version,
        binding_id: "binding-501", ...this.meetingSource,
        proposal_count: proposals.length, proposals, external_actions_created: false,
      } });
    }
    const proposalConfirmMatch = path.match(/^\/management\/meeting-proposals\/(\d+)\/confirm$/);
    if (method === "POST" && proposalConfirmMatch) {
      const proposalId = Number(proposalConfirmMatch[1]);
      const proposal = [...this.meetingProposals.values()].flat().find((item) => item.id === proposalId);
      if (!proposal) return this.fulfill(route, { status: 404, body: { detail: "Meeting proposal not found" } });
      const payload = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      if (payload.expected_record_version !== proposal.record_version) {
        return this.fulfill(route, { status: 409, body: { detail: { code: "record_version_conflict" } } });
      }
      proposal.status = "confirmed"; proposal.record_version = 2;
      proposal.target_entity_type = "task"; proposal.target_entity_id = 901;
      return this.fulfill(route, { body: proposal });
    }
    const localUploadJobMatch = path.match(/^\/local-upload\/projects\/(\d+)\/jobs\/(\d+)$/);
    if (method === "GET" && localUploadJobMatch) {
      return this.fulfill(route, { body: {
        job_id: Number(localUploadJobMatch[2]), status: "completed", progress: 100,
        error: null, result: {
          processed: 1, skipped: 0, tasks: 0, risks: 0,
          decisions: 0, drafts: 0, documents: [],
        },
      } });
    }
    if (method === "PATCH" && /^\/projects\/\d+\/ai-policy$/.test(path)) {
      const current = this.aiPolicies.get(projectId) || {
        project_id: projectId, mode: "local_only", dlp_enabled: true, prompt_version: "v1",
      };
      const patch = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      const saved = { ...current, mode: patch.mode, dlp_enabled: patch.dlp_enabled };
      this.aiPolicies.set(projectId, saved);
      return this.fulfill(route, { body: saved });
    }
    if (method === "GET" && /^\/mail\/projects\/\d+\/capabilities$/.test(path)) {
      return this.fulfill(route, { body: {
        provider: "Gmail", connected: true, features: {
          compose: true, reply: true, reply_all: true, forward: true,
          attachment_send: false, threads: true, explicit_revision_approval: true,
        },
      } });
    }
    if (method === "GET" && /^\/mail\/projects\/\d+\/folders$/.test(path)) {
      return this.fulfill(route, { body: { folders: [
        { id: "inbox", name: "Входящие", count: 0 },
        { id: "attention", name: "Требуют внимания", count: 0 },
        { id: "drafts", name: "Черновики", count: 0 },
        { id: "sent", name: "Отправленные", count: 0 },
      ] } });
    }
    if (method === "GET" && /^\/mail\/projects\/\d+\/threads$/.test(path)) {
      return this.fulfill(route, { body: { threads: [], next_cursor: null } });
    }
    const evidenceTrailMatch = path.match(/^\/api\/v54\/projects\/(\d+)\/evidence-trail$/);
    if (method === "GET" && evidenceTrailMatch) {
      return this.fulfill(route, { body: {
        project_id: Number(evidenceTrailMatch[1]),
        items: [],
        next_cursor: null,
      } });
    }
    if (method === "GET" && /^\/projects\/\d+\/source-folders\/discover$/.test(path)) {
      if (this.discoveryReply) return this.fulfill(route, this.discoveryReply(url));
      const selected = url.searchParams.get("provider");
      if (selected && selected !== this.provider) return this.fulfill(route, { status: 409, body: { detail: "Selected storage provider changed" } });
      return this.fulfill(route, { body: discovery(this.provider, url.searchParams.get("folder_id") ?? this.roots.get(projectId) ?? paths[this.provider][1], projectId) });
    }
    if (method === "POST" && /^\/projects\/\d+\/source-folders\/.+\/snapshot-queue$/.test(path)) {
      const id = decodeURIComponent(path.replace(/^\/projects\/\d+\/source-folders\//, "").replace(/\/snapshot-queue$/, ""));
      const response = this.confirmReply || { body: this.confirm(id, projectId) };
      if (!response.status || response.status === 200) {
        this.roots.set(projectId, id);
        this.snapshots = [{ id: 31, project_id: projectId, status: "building", source_external_id: id,
          source_folder: names[paths[this.provider].indexOf(id)], provider: this.provider, item_count: 0, analysis_status: "pending" }];
      }
      return this.fulfill(route, response);
    }
    if (method === "POST" && /^\/projects\/\d+\/snapshots\/31\/analyze$/.test(path)) return this.fulfill(route, this.analyzeReply);
    if (method === "POST" && /^\/projects\/\d+\/snapshots\/31\/standardize$/.test(path)) return this.fulfill(route, this.standardizeReply);
    if (method !== "GET") return this.block(route, `Unexpected write ${method} ${path}`);
    const single: Record<string, unknown> = {
      "/auth/me": { id: 900, name: "Synthetic Operator", email: "operator@example.invalid", is_admin: true },
      "/mobile-sync/status": { conflicts: [], count: 0 },
      "/organizations/current/requisites": { id: 901, name: "Synthetic Organization", requisites_status: "draft" },
      "/projects/": { projects: this.projectRows },
      "/api/readiness": { ready: true, google_drive_ready: true, telegram_ready: false, checks: {} },
      "/history/audit": { logs: [] },
      "/tasks": { tasks: [] }, "/governance/risks": { risks: [] }, "/governance/decisions": { decisions: [] },
      "/response-drafts": { drafts: [] }, "/ai-secretary/inbox": { messages: this.inboxByProject.get(projectId) || [] }, "/organizer/proposals": { proposals: [] },
      "/ai-secretary/automations": { rules: [] }, "/project-contacts": { contacts: [] },
      "/mail/settings": { display_name: "Synthetic Operator", signature_html: "", auto_signature_new: true,
        auto_signature_reply: true, default_font: "Arial", default_font_size: "14px", default_text_color: "#18211d" },
      "/ai-secretary/daily-briefing": { project_id: projectId, date: "2026-09-03", summary: {
        attention: 0, overdue_tasks: 0, overdue_obligations: 0, open_risks: 0, pending_decisions: 0,
        drafts_waiting_approval: 0, messages_waiting_context: 0,
      }, attention: [], next_step: "Синтетический проект: нет событий", external_actions_created: false },
      "/analytics/project": { summary: { documents: 0, document_coverage: 0, open_tasks: 0, overdue_tasks: 0,
        open_risks: 0, pending_decisions: 0, contracts: 0, active_contracts: 0, messages: 0, pending_messages: 0 },
        documents_by_source: [], documents_by_status: [], tasks_by_status: [], risks_by_criticality: [], messages_by_channel: [] },
      "/execution/document-candidates": { candidates: [] },
      "/execution/cost-categories": { categories: [] },
      "/execution/overview": { budget: [], cash_flow: [], procurement: [], acts: [], baselines: [], schedule: [], summary: {
        budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0, budget_variance: 0, cash_balance_forecast: 0,
        cash_gap: 0, cash_gap_date: null, delayed_schedule: 0, late_procurement: 0, acts_pending: 0, pending_payments: 0, unlinked_invoices: 0,
      } },
      "/management/obligations": { obligations: [] }, "/management/meetings": { meetings: this.meetings }, "/management/notifications": { notifications: [] },
      "/management/notification-policy": { record_version: 1, timezone: "Europe/Moscow", deadline_local_time: "09:00:00", quiet_start: "22:00:00", quiet_end: "07:00:00", escalation_delays: [0, 60], channels: ["in_app"], enabled: true, digest_enabled: false, digest_cadence: "daily", digest_local_time: "09:00:00" },
      "/management/digests": { digests: [], next_cursor: null, external_actions_created: false },
      "/management/attention": { items: this.attentionItems, count: this.attentionItems.length, next_cursor: null },
      "/management/resources": { resources: this.resources },
      "/dashboard/project": { summary: { attention: 0, documents: 0, open_tasks: 0, overdue_tasks: 0, open_risks: 0,
        pending_decisions: 0, drafts: 0, open_obligations: 0, overdue_obligations: 0, upcoming_meetings: 0, unread_notifications: 0 }, documents: [] },
      "/integrations/project": { project_id: projectId, adapters: [
        ...["google_drive", "yandex_disk"].map(provider => ({
          key: provider, provider, capability: "storage", name: provider === "google_drive" ? "Google Drive" : "Яндекс Диск",
          description: "Синтетическое подключение", available: true, connected: true, action: "select_source",
        })),
        { key: "local-upload", provider: "local", capability: "storage", name: "Локальная загрузка", description: "Синтетический локальный путь", available: true, connected: true, action: "local_upload" },
        { key: "ai-policy", provider: "policy", capability: "ai", name: "Политика AI", description: "Синтетическая политика проекта", available: true, connected: true, action: "ai_policy" },
      ] },
    };
    if (path in single) return this.fulfill(route, { body: single[path] });
    const scoped: Record<string, unknown> = {
      snapshots: { snapshots: this.snapshots.filter(row => row.project_id === projectId) },
      "processing-queue": this.queue,
      "site-location": { project_id: projectId, latitude: null, longitude: null },
      "launch-readiness": { project_name: "Новый проект", source_ready: true, documents: 0,
        analyzed_documents: 0, contracts: 0, linked_contracts: 0, schedule_rows: 0,
        budget_rows: 0, cash_flow_rows: 0, contacts: 0, confirmed_contacts: 0, inbox_messages: 0 },
      "google/status": { authorized: true, gmail_authorized: false },
      documents: { documents: [] }, contracts: { contracts: [] },
      members: { members: [{ user_id: 900, role: "manager", name: "Synthetic Operator", email: "operator@example.invalid" }] },
      "ai-policy": this.aiPolicies.get(projectId) || { project_id: projectId, mode: "local_only", dlp_enabled: true, prompt_version: "v1" },
    };
    const suffix = path.replace(/^\/projects\/\d+\//, "");
    if (/^\/projects\/\d+\//.test(path) && suffix in scoped) return this.fulfill(route, { body: scoped[suffix] });
    return this.block(route, `Unexpected ${method} ${path}`);
  }
  private async fulfill(route: Route, reply: Reply) {
    await route.fulfill({ status: reply.status ?? 200, contentType: "application/json", body: JSON.stringify(reply.body) });
  }
  private async block(route: Route, message: string) { this.unexpected.push(message); await route.abort("blockedbyclient"); }
}

export const test = base.extend<{ mock: StorageApi }>({
  mock: async ({ context, page }, use, info) => {
    const mock = new StorageApi();
    page.on("pageerror", error => mock.errors.push(error.message));
    await context.route("**/*", route => mock.route(route));
    await context.routeWebSocket("**/*", socket => { mock.unexpected.push("Unexpected WebSocket"); socket.close(); });
    await context.addCookies([{ name: "pu_csrf", value: "synthetic-csrf-only", url: origin }]);
    await context.addInitScript(() => {
      if (!sessionStorage.getItem("pu_e2e_initialized")) {
        sessionStorage.setItem("pu_active_project_id", "2");
        sessionStorage.setItem("pu_e2e_initialized", "yes");
      }
    });
    await use(mock);
    await info.attach("synthetic-http-protocol", { body: JSON.stringify({ requests: mock.requests,
      unexpected: mock.unexpected, pageErrors: mock.errors }, null, 2), contentType: "application/json" });
    expect(mock.unexpected, "Deny-by-default HTTP/WebSocket boundary").toEqual([]);
    expect(mock.errors, "No application runtime exceptions").toEqual([]);
  },
});
export { expect };
export const picker = (page: Page) => page.locator("#drive-source-picker");
export async function start(page: Page) {
  await page.goto("/new/");
  await expect(page.getByRole("combobox").first()).toHaveValue("2");
  await expect(page.getByRole("button", { name: "Выбрать рабочую папку", exact: true })).toBeVisible();
}
export async function open(page: Page) {
  await page.getByRole("button", { name: /^(Выбрать рабочую папку|Все источники)$/ }).click();
}
export async function settled(page: Page) {
  // Flush response microtasks + React paint, not a guessed sleep duration.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
export async function release(page: Page, hold: ReturnType<StorageApi["hold"]>, body: unknown, status = 200) {
  const request = await hold.request;
  const response = page.waitForResponse(value => value.request() === request);
  hold.release({ body, status });
  await response; await settled(page);
}
