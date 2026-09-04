import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { MailClientModule, readableMessageBody } from "./MailClientModule";
import { editorHtml } from "./RichTextEditor";
import type { MailCapabilities, MailDraft, MailMessage, MailThread } from "./types";

const capabilities: MailCapabilities = {
  provider: "Gmail", connected: true, can_send: true, can_compose: true,
  can_reply: true, can_reply_all: true, can_forward: true, can_attach: false,
  supports_threads: true, versioned_approval: true,
};

const approvedDraft: MailDraft = {
  id: 41, project_id: 7, contract_id: 11, message_id: 101, mode: "reply",
  to: [{ email: "partner@example.test" }], cc: [], bcc: [], subject: "Re: Срок поставки",
  body: "Подтверждаем получение.", attachments: [], revision: 2, approved_revision: 2,
  status: "approved", receipt: null,
};

const message: MailMessage = {
  id: 101, project_id: 7, contract_id: 11, thread_id: "thread-101", direction: "incoming",
  sender: { email: "partner@example.test", name: "Партнёр" },
  to: [{ email: "operator@example.test" }], cc: [{ email: "manager@example.test" }],
  subject: "Срок поставки", preview: "Просим подтвердить срок", content: "Просим подтвердить срок поставки до 10 сентября.",
  summary: "Нужно подтвердить срок поставки.", received_at: "2026-09-04T10:00:00Z",
  status: "ready", needs_attention: true, context_confirmed: true,
  context_confidence: .96, context_evidence: "Найден номер договора", source_url: "https://example.test/message/101",
  attachments: [{ attachment_id: "att-1", name: "specification.pdf", mime_type: "application/pdf", size: 2048 }],
  drafts: [approvedDraft],
};

const thread: MailThread = {
  id: "thread-101", subject: message.subject, participants: [message.sender, ...message.to],
  last_message_at: message.received_at, unread_count: 1, needs_attention: true, messages: [message],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mockClient(overrides: Record<string, unknown> = {}) {
  return {
    capabilities: vi.fn().mockResolvedValue(capabilities),
    folders: vi.fn().mockResolvedValue({ folders: [
      { kind: "inbox", label: "Входящие", count: 1 },
      { kind: "attention", label: "Требуют внимания", count: 1 },
      { kind: "drafts", label: "Черновики", count: 1 },
      { kind: "sent", label: "Отправленные", count: 0 },
      { kind: "archive", label: "Архив", count: 0 },
    ] }),
    messages: vi.fn().mockResolvedValue({ items: [message] }),
    threads: vi.fn().mockResolvedValue({ items: [thread] }),
    thread: vi.fn().mockResolvedValue(thread),
    message: vi.fn().mockResolvedValue(message),
    createDraft: vi.fn().mockResolvedValue({ ...approvedDraft, id: 44, mode: "compose", revision: 1, approved_revision: null, status: "draft" }),
    updateDraft: vi.fn().mockImplementation(async (_id: number, draft: MailDraft) => ({ ...draft, revision: draft.revision + 1, approved_revision: null, status: "draft" })),
    approveDraft: vi.fn().mockImplementation(async (_id: number, revision: number) => ({ ...approvedDraft, revision, approved_revision: revision, status: "approved" })),
    sendDraft: vi.fn().mockResolvedValue({ ...approvedDraft, status: "sent", receipt: { provider: "gmail", sent_at: "2026-09-04T10:05:00Z" } }),
    confirmContext: vi.fn().mockResolvedValue(message),
    setMessageStatus: vi.fn().mockResolvedValue({ ...message, status: "in_progress" }),
    settings: vi.fn().mockResolvedValue({
      display_name: "Operator", signature_html: "", auto_signature_new: true, auto_signature_reply: true,
      default_font: "Arial", default_font_size: "14px", default_text_color: "#18211d",
    }),
    updateSettings: vi.fn().mockImplementation(async (settings) => settings),
    assist: vi.fn().mockResolvedValue({
      subject: "Подтверждение срока", body: "Добрый день! Подтверждаем получение запроса.",
      notes: "Проверьте срок.", provider: "gemini", model: "gemini-test", policy_mode: "external_allowed", requires_confirmation: true,
    }),
    moveMessage: vi.fn().mockResolvedValue(message),
    ...overrides,
  };
}

function renderClient(client = mockClient(), propOverrides: Record<string, unknown> = {}) {
  const props = {
    projectId: 7,
    currentUserEmail: "operator@example.test",
    projects: [{ id: 7, name: "Дубна" }, { id: 8, name: "Городец" }],
    contracts: [{ id: 11, project_id: 7, number: "ГП-17", title: "Генподряд" }],
    syncing: false,
    syncStatus: "",
    onSync: vi.fn(),
    onOpenContacts: vi.fn(),
    onNotice: vi.fn(),
    onError: vi.fn(),
    client: client as never,
    ...propOverrides,
  };
  return { ...render(<MailClientModule {...props} />), client, props };
}

describe("MailClientModule", () => {
  it("keeps sync feedback inside the compact header area", async () => {
    const { container } = renderClient(mockClient(), { syncStatus: "Проверено 16:06. Новых: 0." });
    await screen.findByText("Проверено 16:06. Новых: 0.");

    const client = container.querySelector(".mail-client");
    const top = container.querySelector(".mail-client-top");
    const status = container.querySelector(".mail-sync-status");
    const layout = container.querySelector(".mail-layout");

    expect(client?.children[0]).toBe(top);
    expect(client?.children[1]).toBe(layout);
    expect(top).toContainElement(status as HTMLElement);
  });

  it("turns HTML email into readable safe text instead of showing source markup", () => {
    const body = readableMessageBody("<html><style>.hidden{display:none}</style><p>Добрый день!<br>Срок — 10 сентября.</p><script>secret()</script></html>");
    expect(body).toBe("Добрый день!\nСрок — 10 сентября.");
    expect(body).not.toContain("<p>");
    expect(body).not.toContain("secret");
  });

  it("removes legacy CSS that was previously stored as plain email text", () => {
    const body = readableMessageBody("96 Timeweb Cloud html { -webkit-text-size-adjust: none; } p { margin: 0 !important; } @media screen and (max-width: 600px) { .container { width: 100% !important; } } Проверьте, что-то не так с картой");
    expect(body).toBe("96 Timeweb Cloud\nПроверьте, что-то не так с картой");
    expect(body).not.toContain("!important");
    expect(body).not.toContain("@media");
  });

  it("loads a real thread view with AI summary, context and inbound attachments", async () => {
    const { client } = renderClient();
    expect(await screen.findByRole("heading", { name: "Срок поставки" })).toBeInTheDocument();
    expect(screen.getByText("Нужно подтвердить срок поставки.")).toBeInTheDocument();
    expect(screen.getByText("specification.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Требуют внимания/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Черновики/ })).toBeInTheDocument();
    expect(screen.queryByText(/не добавляются автоматически/i)).not.toBeInTheDocument();
    expect(client.threads).toHaveBeenCalledWith(7, "inbox", "");
  });

  it("switches folders through the backend contract and never invents local sent mail", async () => {
    const client = mockClient();
    renderClient(client);
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: /Отправленные/ }));
    await waitFor(() => expect(client.threads).toHaveBeenLastCalledWith(7, "sent", ""));
  });

  it("reloads the mailbox after the provider sync completes", async () => {
    const { client, props } = renderClient();
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: /Получить новые/ }));
    await waitFor(() => expect(props.onSync).toHaveBeenCalledOnce());
    await waitFor(() => expect(client.threads).toHaveBeenCalledTimes(2));
  });

  it("confirms the exact approved revision before send", async () => {
    const { client } = renderClient();
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: /Re: Срок поставки/ }));
    const composer = screen.getByRole("dialog", { name: /Черновик · версия 2/ });
    fireEvent.click(within(composer).getByRole("button", { name: "Отправить" }));
    const confirmation = screen.getByRole("alertdialog");
    expect(within(confirmation).getByRole("heading", { name: /версию 2/i })).toBeInTheDocument();
    expect(within(confirmation).getByText("partner@example.test")).toBeInTheDocument();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Отправить версию 2" }));
    await waitFor(() => expect(client.sendDraft).toHaveBeenCalledWith(41, 2, expect.any(String)));
  });

  it("invalidates approval visibly when the text changes", async () => {
    renderClient();
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: /Re: Срок поставки/ }));
    const composer = screen.getByRole("dialog");
    const editor = within(composer).getByLabelText("Текст письма");
    editor.innerHTML = "<div>Исправленный ответ</div>";
    fireEvent.input(editor);
    expect(within(composer).getByText(/подтвердите новую версию/i)).toBeInTheDocument();
    expect(within(composer).getByRole("button", { name: "Отправить" })).toBeDisabled();
    expect(within(composer).getByRole("button", { name: "Подтвердить текущую версию" })).toBeDisabled();
  });

  it("creates a compose draft with cc/bcc and keeps it explicitly unsent", async () => {
    const { client, props } = renderClient();
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: /Написать/ }));
    const composer = screen.getByRole("dialog");
    fireEvent.change(within(composer).getByLabelText("Кому"), { target: { value: "one@example.test" } });
    fireEvent.click(within(composer).getByRole("button", { name: "Копия / скрытая" }));
    fireEvent.change(within(composer).getByLabelText("Копия"), { target: { value: "copy@example.test" } });
    fireEvent.change(within(composer).getByLabelText("Скрытая копия"), { target: { value: "secret@example.test" } });
    fireEvent.change(within(composer).getByLabelText("Тема"), { target: { value: "Протокол" } });
    const editor = within(composer).getByLabelText("Текст письма");
    editor.innerHTML = "<div>Направляю протокол.</div>";
    fireEvent.input(editor);
    fireEvent.click(within(composer).getByRole("button", { name: "Сохранить черновик" }));
    await waitFor(() => expect(client.createDraft).toHaveBeenCalledWith(expect.objectContaining({
      project_id: 7, to: ["one@example.test"], cc: ["copy@example.test"],
      bcc: ["secret@example.test"], subject: "Протокол", body: "<div>Направляю протокол.</div>", body_format: "html",
    })));
    expect(props.onNotice).toHaveBeenCalledWith(expect.stringContaining("не отправлен"));
  });

  it("prepares reply-all without adding the current mailbox as a recipient", async () => {
    renderClient();
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: "Всем" }));
    const composer = screen.getByRole("dialog");
    expect(within(composer).getByLabelText("Кому")).toHaveValue("partner@example.test");
    fireEvent.click(within(composer).getByRole("button", { name: "Копия / скрытая" }));
    expect(within(composer).getByLabelText("Копия")).toHaveValue("manager@example.test");
    expect((within(composer).getByLabelText("Кому") as HTMLInputElement).value).not.toContain("operator@example.test");
  });

  it("shows a safe conflict instead of retrying a stale draft", async () => {
    const updateDraft = vi.fn().mockRejectedValue(new ApiError("Conflict", 409, "test-request"));
    const { props } = renderClient(mockClient({ updateDraft }));
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: /Re: Срок поставки/ }));
    const composer = screen.getByRole("dialog");
    const editor = within(composer).getByLabelText("Текст письма");
    editor.innerHTML = "<div>Изменено</div>";
    fireEvent.input(editor);
    fireEvent.click(within(composer).getByRole("button", { name: "Сохранить черновик" }));
    await waitFor(() => expect(props.onError).toHaveBeenCalledWith(expect.stringContaining("другой вкладке")));
    expect(updateDraft).toHaveBeenCalledTimes(1);
  });

  it("offers Outlook-style formatting, account signature settings and Gemini drafting", async () => {
    const client = mockClient();
    renderClient(client);
    await screen.findByRole("heading", { name: "Срок поставки" });

    fireEvent.click(screen.getByRole("button", { name: /Настройки/ }));
    const settings = screen.getByRole("dialog", { name: "Настройки и подпись" });
    expect(within(settings).getByLabelText("Подпись")).toBeInTheDocument();
    fireEvent.click(within(settings).getByRole("button", { name: /Сохранить настройки/ }));
    await waitFor(() => expect(client.updateSettings).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole("button", { name: /Написать/ }));
    const composer = screen.getByRole("dialog", { name: /Новый черновик/ });
    expect(within(composer).getByRole("toolbar", { name: "Форматирование письма" })).toBeInTheDocument();
    expect(within(composer).getByRole("button", { name: /Полужирный/ })).toBeInTheDocument();
    expect(within(composer).getByRole("button", { name: /Подчёркивание/ })).toBeInTheDocument();
    fireEvent.click(within(composer).getByRole("button", { name: "Помощь Gemini" }));
    fireEvent.change(within(composer).getByPlaceholderText(/Опишите, кому/), { target: { value: "Подтвердить срок" } });
    fireEvent.click(within(composer).getByRole("button", { name: "Написать письмо" }));
    await waitFor(() => expect(client.assist).toHaveBeenCalledWith(expect.objectContaining({ action: "compose", instruction: "Подтвердить срок" })));
    await waitFor(() => expect((within(composer).getByLabelText("Текст письма") as HTMLElement).innerHTML).toContain("Подтверждаем получение"));
    expect(client.sendDraft).not.toHaveBeenCalled();
  });

  it("removes active content from rich editor HTML before it is rendered", () => {
    const html = editorHtml('<div onclick="alert(1)">Текст<img src="https://tracker.test/pixel" onerror="alert(2)"><script>alert(3)</script><a href="javascript:alert(4)">ссылка</a></div>');
    expect(html).toContain("Текст");
    expect(html).toContain("ссылка");
    expect(html).not.toMatch(/onclick|onerror|script|javascript:|tracker\.test/i);
  });

  it("moves a message to Gmail trash only after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const client = mockClient();
    renderClient(client);
    await screen.findByRole("heading", { name: "Срок поставки" });
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(client.moveMessage).toHaveBeenCalledWith(101, "trash"));
    expect(confirm).toHaveBeenCalledOnce();
    confirm.mockRestore();
  });

  it("blocks compose and send when the adapter lacks capabilities", async () => {
    renderClient(mockClient({ capabilities: vi.fn().mockResolvedValue({ ...capabilities, can_compose: false, can_send: false, versioned_approval: false }) }));
    await screen.findByRole("heading", { name: "Срок поставки" });
    expect(screen.getByRole("button", { name: /Написать/ })).toBeDisabled();
    expect(screen.getByText(/не поддерживается подключённым адаптером/i)).toBeInTheDocument();
  });

  it("ignores a late response from the previous project", async () => {
    let releaseFirst: (value: { items: MailThread[] }) => void = () => undefined;
    const first = new Promise<{ items: MailThread[] }>((resolve) => { releaseFirst = resolve; });
    const client = mockClient({
      threads: vi.fn()
        .mockReturnValueOnce(first)
        .mockResolvedValueOnce({ items: [{ ...thread, id: "project-8", subject: "Письмо нового проекта" }] }),
    });
    const props = {
      projectId: 7, currentUserEmail: "operator@example.test", projects: [{ id: 7, name: "Дубна" }, { id: 8, name: "Городец" }],
      contracts: [], syncing: false, onSync: vi.fn(), onOpenContacts: vi.fn(),
      onNotice: vi.fn(), onError: vi.fn(), client: client as never,
    };
    const view = render(<MailClientModule {...props} />);
    view.rerender(<MailClientModule {...props} projectId={8} />);
    expect(await screen.findByRole("heading", { name: "Письмо нового проекта" })).toBeInTheDocument();
    releaseFirst({ items: [thread] });
    await Promise.resolve();
    expect(screen.queryByRole("heading", { name: "Срок поставки" })).not.toBeInTheDocument();
  });
});
