import { test as base, expect, type Page, type Request, type Route } from "@playwright/test";

export type Provider = "google_drive" | "yandex_disk";
export type Reply = { status?: number; body: unknown };
type ReplyFactory = (request: Request) => Reply;
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
  attachmentImportReply: Reply = { body: { name: "synthetic.pdf", already_indexed: false, tasks: 1, risks: 0, drafts: 0 } };
  localUploadReplies: Reply[] = [{ body: { processed: 1, tasks: 1, risks: 0, skipped: [] } }];
  roots = new Map<number, string>();
  snapshots: Record<string, unknown>[] = [];
  queue: { summary: { active: number; failed: number; dead_letter: number }; snapshots: unknown[]; sessions: unknown[] } = emptyQueue();
  discoveryReply?: (url: URL) => Reply;
  confirmReply?: Reply;
  analyzeReply: Reply = { body: { snapshot_id: 31, status: "analyzing", already_queued: true } };
  standardizeReply: Reply = { body: { snapshot_id: 31, session_id: 42, status: "retrying", already_queued: true } };
  currentUser: Record<string, unknown> = { id: 900, name: "Synthetic Operator", email: "operator@example.invalid", is_admin: true };
  membersByProject = new Map<number, Record<string, unknown>[]>();
  customReplies = new Map<string, Reply | ReplyFactory>();
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
  reply(method: string, path: string, value: Reply | ReplyFactory) {
    this.customReplies.set(`${method.toUpperCase()} ${path}`, value);
  }
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
    const custom = this.customReplies.get(`${method} ${path}${url.search}`);
    if (custom) return this.fulfill(route, typeof custom === "function" ? custom(request) : custom);
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
    if (method === "PATCH" && /^\/projects\/\d+\/ai-policy$/.test(path)) {
      const current = this.aiPolicies.get(projectId) || {
        project_id: projectId, mode: "local_only", dlp_enabled: true, prompt_version: "v1",
      };
      const patch = JSON.parse(request.postData() || "{}") as Record<string, unknown>;
      const saved = { ...current, mode: patch.mode, dlp_enabled: patch.dlp_enabled };
      this.aiPolicies.set(projectId, saved);
      return this.fulfill(route, { body: saved });
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
      "/auth/me": this.currentUser,
      "/organizations/current/requisites": { id: 901, name: "Synthetic Organization", requisites_status: "draft" },
      "/projects/": { projects: this.projectRows },
      "/api/readiness": { ready: true, google_drive_ready: true, telegram_ready: false, checks: {} },
      "/history/audit": { logs: [] },
      "/tasks": { tasks: [] }, "/governance/risks": { risks: [] }, "/governance/decisions": { decisions: [] },
      "/provider-actions": { items: [], count: 0 },
      "/response-drafts": { drafts: [] }, "/ai-secretary/inbox": { messages: this.inboxByProject.get(projectId) || [] }, "/organizer/proposals": { proposals: [] },
      "/ai-secretary/automations": { rules: [] }, "/project-contacts": { contacts: [] },
      "/ai-secretary/daily-briefing": { project_id: projectId, date: "2026-09-03", summary: {
        attention: 0, overdue_tasks: 0, overdue_obligations: 0, open_risks: 0, pending_decisions: 0,
        drafts_waiting_approval: 0, messages_waiting_context: 0,
      }, attention: [], next_step: "Синтетический проект: нет событий", external_actions_created: false },
      "/analytics/project": { summary: { documents: 0, document_coverage: 0, open_tasks: 0, overdue_tasks: 0,
        open_risks: 0, pending_decisions: 0, contracts: 0, active_contracts: 0, messages: 0, pending_messages: 0 },
        documents_by_source: [], documents_by_status: [], tasks_by_status: [], risks_by_criticality: [], messages_by_channel: [] },
      "/execution/document-candidates": { candidates: [] },
      "/execution/overview": { budget: [], cash_flow: [], procurement: [], acts: [], baselines: [], schedule: [], summary: {
        budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0, budget_variance: 0, cash_balance_forecast: 0,
        cash_gap: 0, cash_gap_date: null, delayed_schedule: 0, late_procurement: 0, acts_pending: 0, pending_payments: 0, unlinked_invoices: 0,
      } },
      "/management/obligations": { obligations: [], count: 0 }, "/management/meetings": { meetings: [] }, "/management/notifications": { notifications: [] },
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
    if (method === "GET" && path === "/management/v2/attention") return this.fulfill(route, { body: {
      items: [], total: 0, offset: 0, limit: 50, generated_at: "2026-09-05T10:00:00Z", external_actions_created: false,
    } });
    if (method === "GET" && /^\/management\/v2\/projects\/\d+\/digest-preference$/.test(path)) {
      return this.fulfill(route, { body: { project_id: projectId, user_id: Number(this.currentUser.id) || 900,
        timezone: "Europe/Moscow", quiet_start: "20:00:00", quiet_end: "08:00:00", channel: "in_app",
        cadence: "daily", record_version: 0, persisted: false, external_actions_enabled: false } });
    }
    if (method === "GET" && /^\/projects\/\d+$/.test(path)) {
      return this.fulfill(route, { body: { id: projectId, organization_id: 901, name: `Synthetic project ${projectId}` } });
    }
    if (method === "GET" && path === "/api/mvp4/supply") {
      return this.fulfill(route, { body: { items: [], total: 0 } });
    }
    if (path in single) return this.fulfill(route, { body: single[path] });
    const scoped: Record<string, unknown> = {
      snapshots: { snapshots: this.snapshots.filter(row => row.project_id === projectId) },
      "processing-queue": this.queue,
      "google/status": { authorized: true, gmail_authorized: false },
      documents: { documents: [] }, contracts: { contracts: [] }, members: { members: this.membersByProject.get(projectId) || [] },
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
