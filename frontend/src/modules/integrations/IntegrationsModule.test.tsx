import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IntegrationsModule, type IntegrationItem } from "./IntegrationsModule";

const items: IntegrationItem[] = [
  { key: "drive", provider: "google_workspace", capability: "storage", name: "Google Drive", description: "Документы", available: true, connected: true, action: "select_source" },
  { key: "yandex-mail", provider: "yandex_mail", capability: "channel", name: "Яндекс Почта", description: "Входящие", available: true, connected: false, action: "configure" },
  { key: "local", provider: "local", capability: "storage", name: "Локальная папка", description: "Файлы", available: false, connected: false, action: "local_upload" },
];

afterEach(cleanup);

describe("IntegrationsModule", () => {
  it("presents integrations as a connection contour with status totals", () => {
    const onConnectProvider = vi.fn();
    render(<IntegrationsModule
      collapsed={false}
      items={items}
      systemState={null}
      gmailSyncing={false}
      gmailSyncStatus=""
      syncingProvider=""
      onSyncChannel={vi.fn()}
      onSelectFolder={vi.fn()}
      onConnectProvider={onConnectProvider}
      onLocalUpload={vi.fn()}
      onOpenAIPolicy={vi.fn()}
      onOpenGmailResults={vi.fn()}
      onReload={vi.fn()}
    />);

    expect(screen.getByText("Контур данных проекта")).toBeInTheDocument();
    expect(screen.getByText("Источники и сервисы")).toBeInTheDocument();
    expect(screen.getByText("Диагностика контура")).toBeInTheDocument();
    expect(screen.getByText("подключено").previousElementSibling).toHaveTextContent("1");
    expect(screen.getByText("ожидает").previousElementSibling).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Загрузить папку" })).toBeDisabled();
    screen.getByRole("button", { name: "Подключить" }).click();
    expect(onConnectProvider).toHaveBeenCalledWith("yandex_mail");
  });
});
