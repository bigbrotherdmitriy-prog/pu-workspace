import { afterEach, describe, expect, it, vi } from "vitest";
import { mailClientApi } from "./mailClientApi";
import type { MailDraft } from "./types";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("mailClientApi contract adapter", () => {
  it("maps backend capability flags and preserves all core folders", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        provider: "google_workspace", connected: true,
        features: { compose: true, reply: true, reply_all: true, forward: true, attachment_send: false, threads: true, explicit_revision_approval: true },
      }))
      .mockResolvedValueOnce(response({ folders: [
        { id: "inbox", name: "Входящие", kind: "core", count: 9 },
        { id: "attention", name: "Требуют внимания", kind: "core", count: 2 },
        { id: "drafts", name: "Черновики", kind: "core", count: 3 },
        { id: "sent", name: "Отправленные", kind: "core" },
        { id: "archive", name: "Архив", kind: "core" },
        { id: "all", name: "Вся почта", kind: "core" },
        { id: "provider-label", name: "Поставщики", kind: "provider" },
      ] }));
    vi.stubGlobal("fetch", fetchMock);

    const capabilities = await mailClientApi.capabilities(7);
    const folders = await mailClientApi.folders(7);
    expect(capabilities).toMatchObject({ provider: "google_workspace", can_compose: true, can_attach: false, versioned_approval: true });
    expect(folders.folders.map((item) => item.kind)).toEqual(["inbox", "attention", "drafts", "sent", "archive", "all"]);
    expect(folders.folders.find((item) => item.kind === "attention")?.count).toBe(2);
  });

  it("normalizes list previews and complete thread messages", async () => {
    const rawMessage = {
      id: 101, project_id: 7, contract_id: 11, provider: "google_workspace", direction: "incoming",
      thread_id: "provider-thread", subject: "КС-2", sender: "Подрядчик <partner@example.test>",
      content: "Просим согласовать акт.", summary: "Требуется согласование.", labels: ["INBOX"],
      headers: { to: "operator@example.test", cc: "manager@example.test" }, attachments: [],
      status: "ready", context_confirmed: true, created_at: "2026-09-04T10:00:00Z", drafts: [],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ threads: [{ thread_id: "provider-thread", message_count: 1, latest: rawMessage }] }))
      .mockResolvedValueOnce(response({ thread_id: "provider-thread", messages: [rawMessage], count: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    const list = await mailClientApi.threads(7, "inbox", "акт");
    const detail = await mailClientApi.thread(7, "provider-thread");
    expect(list.items[0]).toMatchObject({ id: "provider-thread", subject: "КС-2" });
    expect(detail.messages[0]).toMatchObject({ sender: { email: "Подрядчик <partner@example.test>" }, to: [{ email: "operator@example.test" }] });
    expect(fetchMock.mock.calls[0][0]).toContain("folder=inbox&query=%D0%B0%D0%BA%D1%82");
  });

  it("loads standalone proactive drafts from the dedicated drafts endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ drafts: [{
      id: 77, project_id: 7, contract_id: null, provider: "google_workspace", mode: "compose",
      reply_to_message_id: null, to: ["client@example.test"], cc: [], bcc: [], subject: "Ежемесячный отчёт",
      body: "Направляем отчёт.", attachments: [], revision: 1, approved_revision: null, status: "draft",
      send_attempts: 0, error_code: null, receipt: null, created_at: "2026-09-04T08:00:00Z", updated_at: "2026-09-04T08:00:00Z",
    }] }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await mailClientApi.threads(7, "drafts", "отчёт");
    expect(fetchMock.mock.calls[0][0]).toBe("/mail/projects/7/drafts");
    expect(result.items[0]).toMatchObject({ id: "draft:77", subject: "Ежемесячный отчёт" });
    expect(result.items[0].messages[0].drafts[0]).toMatchObject({ id: 77, revision: 1, status: "draft" });
  });

  it("sends CAS revision and idempotency key without leaking unknown provider errors", async () => {
    const rawDraft = {
      id: 41, project_id: 7, contract_id: null, provider: "google_workspace", mode: "compose",
      reply_to_message_id: null, to: ["partner@example.test"], cc: [], bcc: [], subject: "Тема", body: "Текст",
      attachments: [], revision: 3, approved_revision: 3, status: "unknown", send_attempts: 1,
      error_code: "provider_outcome_unknown", receipt: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(response(rawDraft));
    vi.stubGlobal("fetch", fetchMock);
    const result = await mailClientApi.sendDraft(41, 3, "send-key-123");
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({ revision: 3, idempotency_key: "send-key-123" });
    expect(result.safe_error).toContain("Автоматический повтор заблокирован");
    expect(result.safe_error).not.toContain("provider_outcome_unknown");
  });

  it("patches only editable draft fields and the expected revision", async () => {
    const draft: MailDraft = {
      id: 41, project_id: 7, contract_id: 11, message_id: null, mode: "compose",
      to: [{ email: "one@example.test" }], cc: [{ email: "copy@example.test" }], bcc: [],
      subject: "Тема", body: "Текст", attachments: [], revision: 2, approved_revision: null, status: "draft",
    };
    const fetchMock = vi.fn().mockResolvedValue(response({
      ...draft, to: ["one@example.test"], cc: ["copy@example.test"], bcc: [], provider: "google_workspace",
      reply_to_message_id: null, send_attempts: 0, error_code: null,
    }));
    vi.stubGlobal("fetch", fetchMock);
    await mailClientApi.updateDraft(41, draft);
    const payload = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(payload).toMatchObject({ expected_revision: 2, contract_id: 11, to: ["one@example.test"] });
    expect(payload).not.toHaveProperty("project_id");
    expect(payload).not.toHaveProperty("attachments");
  });
});
