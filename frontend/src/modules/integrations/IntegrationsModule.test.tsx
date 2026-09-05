import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IntegrationsModule, type IntegrationItem } from "./IntegrationsModule";

const items: IntegrationItem[] = [
  { key: "drive", provider: "google_workspace", capability: "storage", name: "Google Drive", description: "Документы", available: true, connected: true, action: "select_source" },
  { key: "local", provider: "local", capability: "storage", name: "Локальная папка", description: "Файлы", available: true, connected: false, action: "local_upload" },
];

afterEach(cleanup);

describe("IntegrationsModule", () => {
  it("presents integrations as a connection contour with status totals", () => {
    render(<IntegrationsModule
      collapsed={false}
      items={items}
      systemState={null}
      gmailSyncing={false}
      gmailSyncStatus=""
      onSyncGmail={vi.fn()}
      onSelectFolder={vi.fn()}
      onConnectProvider={vi.fn()}
      onLocalUpload={vi.fn()}
      onOpenAIPolicy={vi.fn()}
      onOpenGmailResults={vi.fn()}
      onReload={vi.fn()}
    />);

    expect(screen.getByText("Контур данных проекта")).toBeInTheDocument();
    expect(screen.getByText("Источники и сервисы")).toBeInTheDocument();
    expect(screen.getByText("Диагностика контура")).toBeInTheDocument();
    expect(screen.getAllByText("1")).toHaveLength(2);
  });
});
