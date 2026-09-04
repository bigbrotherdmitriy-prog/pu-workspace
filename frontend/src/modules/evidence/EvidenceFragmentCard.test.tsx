import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvidenceFragmentCard } from "./EvidenceFragmentCard";
import { EVIDENCE_FRAGMENT_SCHEMA_VERSION } from "./evidenceReadModel";

function validInput() {
  return {
    schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION as string,
    capabilities: { metadata: "allow", fragment: "allow", archival_fragment: "deny" },
    policy: { known: true, version: "policy-7" as string | null },
    evidence: {
      id: "evidence-16",
      revision: 1,
      source_id: "source-13",
      source_version_id: "version-15",
      status: "verified",
    },
    source: {
      id: "source-13",
      record_version: 4,
      current_source_version_id: "version-15",
      version_state: "current",
      freshness: "fresh",
      availability: "available",
      provider: "google-workspace",
      account: "project-mailbox",
      namespace: "mailbox-primary",
      origin_project: "Альфа",
    },
    source_version: { id: "version-15", revision: 1, source_id: "source-13" },
    locator: { kind: "page", page: 2 } as Record<string, unknown>,
    fragment: { media_type: "text/plain", excerpt: "Оплатить до 10 сентября." },
    extracted_fact: "Срок: 10.09.2026",
    ai_conclusion: "Возможно, нужна внутренняя задача.",
    extractor: {
      name: "local-ocr",
      version: "2",
      method: "ocr",
      model_provider: "local",
      model_id: "classifier",
      model_version: "1",
      prompt_version: "prompt-3",
    },
    confidence: { value: 0.923, kind: "calibrated", calibration_ref: "calibration-2" },
    assessment: {
      verification: "verified",
      reviewer: "Мария" as string | null,
      reviewed_at: "2026-09-03T09:01:00Z" as string | null,
      record_version: 3,
    },
    historical: false,
    reason_code: null as string | null,
  };
}

afterEach(cleanup);

function assertSensitiveContentAbsent(container: HTMLElement, markers: string[]) {
  const serialized = container.outerHTML;
  for (const marker of markers) expect(serialized).not.toContain(marker);
  for (const element of container.querySelectorAll("*")) {
    for (const attribute of element.getAttributeNames()) {
      const value = element.getAttribute(attribute) ?? "";
      for (const marker of markers) expect(value).not.toContain(marker);
    }
  }
  expect(container.querySelectorAll("[hidden], [aria-live], [role='alert'], [title], [aria-label]")).toHaveLength(0);
}

describe("EvidenceFragmentCard", () => {
  it("renders verified current readable evidence with separated sections", () => {
    render(<EvidenceFragmentCard input={validInput()} />);
    expect(screen.getByRole("heading", { name: "Основание вывода" })).toBeInTheDocument();
    expect(screen.getByText("evidence-16/r1")).toBeInTheDocument();
    expect(screen.getByText("version-15/r1")).toBeInTheDocument();
    expect(screen.getByText("Оплатить до 10 сентября.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Извлечённый факт" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Вывод AI" })).toBeInTheDocument();
    expect(screen.getByText(/не гарантия истинности/i)).toBeInTheDocument();
  });

  it("labels a readable unverified fragment for human review", () => {
    const input = validInput();
    input.evidence.status = "unverified";
    input.assessment = { verification: "unverified", reviewer: null, reviewed_at: null, record_version: 1 };
    render(<EvidenceFragmentCard input={input} />);
    expect(screen.getAllByText("Не проверено").length).toBeGreaterThan(0);
    expect(screen.getByText("Оплатить до 10 сентября.")).toBeInTheDocument();
    expect(screen.getByText(/Confidence не заменяет решение человека/)).toBeInTheDocument();
  });

  it.each([
    ["deny", "deny"],
    ["stale", "stale"],
    ["unavailable", "unavailable"],
    ["revoked", "revoked"],
    ["expired", "expired"],
    ["purged", "purged"],
  ])("removes fragment and derived sensitive text for %s", (_label, state) => {
    const input = validInput();
    if (state === "deny") input.capabilities.fragment = "deny";
    if (state === "stale") { input.evidence.status = "stale"; input.source.freshness = "stale"; input.reason_code = "source_revision_changed"; }
    if (state === "unavailable") { input.evidence.status = "unavailable"; input.source.availability = "unavailable"; input.reason_code = "provider_unavailable"; }
    if (state === "revoked") { input.evidence.status = "unavailable"; input.source.availability = "revoked"; input.reason_code = "access_revoked"; }
    if (state === "expired") { input.evidence.status = "unavailable"; input.source.availability = "expired"; input.reason_code = "fragment_expired"; }
    if (state === "purged") { input.evidence.status = "unavailable"; input.source.availability = "purged"; input.reason_code = "fragment_purged"; }
    const { container } = render(<EvidenceFragmentCard input={input} />);
    assertSensitiveContentAbsent(container, ["Оплатить до 10 сентября.", "Срок: 10.09.2026", "Возможно, нужна внутренняя задача."]);
  });

  it("hides existence details and every sensitive string when metadata is denied", () => {
    const input = validInput();
    input.capabilities.metadata = "deny";
    const { container } = render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByRole("heading", { name: "Доказательство недоступно" })).toBeInTheDocument();
    assertSensitiveContentAbsent(container, ["evidence-16", "source-13", "version-15", "google-workspace", "project-mailbox", "mailbox-primary", "Альфа", "Оплатить", "10.09.2026", "Возможно"]);
  });

  it("removes previously visible content after a denied rerender", () => {
    const visible = validInput();
    const { container, rerender } = render(<EvidenceFragmentCard input={visible} />);
    expect(container).toHaveTextContent("Оплатить до 10 сентября.");
    const denied = validInput();
    denied.capabilities.metadata = "deny";
    rerender(<EvidenceFragmentCard input={denied} />);
    assertSensitiveContentAbsent(container, ["Оплатить", "Срок: 10.09.2026", "evidence-16", "source-13"]);
  });

  it("renders malicious HTML as inert text", () => {
    const input = validInput();
    input.fragment.excerpt = '<img src=x onerror="globalThis.pwned=true"><script>alert(1)</script>';
    input.extracted_fact = "<b>Не доверять</b>";
    const { container } = render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByText(input.fragment.excerpt)).toBeInTheDocument();
    expect(screen.getByText(input.extracted_fact)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("shows a generic safe state for malformed input without echoing it", () => {
    const secret = "SENSITIVE-EXCERPT-DO-NOT-ECHO";
    const { container } = render(<EvidenceFragmentCard input={{ schema_version: "broken", excerpt: secret, title: secret }} />);
    expect(screen.getByText("Не удалось безопасно проверить доказательство.")).toBeInTheDocument();
    assertSensitiveContentAbsent(container, [secret]);
  });

  it("shows the historical warning only for explicitly authorized archival content", () => {
    const input = validInput();
    input.historical = true;
    input.source.version_state = "historical";
    input.source.current_source_version_id = "version-new";
    input.capabilities.archival_fragment = "allow";
    render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByText(/Историческое доказательство/)).toBeInTheDocument();
    expect(screen.getByText("Оплатить до 10 сентября.")).toBeInTheDocument();
  });

  it("shows when precise navigation is unavailable", () => {
    const input = validInput();
    input.locator = { kind: "page_bbox", page: 1, coordinate_space: "pixels", box: [10, 20, 30, 40], extent: [100, 100], representation_id: "raster-1", precise_navigation: false };
    render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByText(/Точная навигация недоступна/)).toBeInTheDocument();
  });

  it("does not fetch, persist, parse HTML or expose mutation controls", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = render(<EvidenceFragmentCard input={validInput()} />);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(container.querySelector("button, input, select, textarea, form")).toBeNull();
    expect(container.innerHTML).not.toContain("dangerouslySetInnerHTML");
    expect(container.innerHTML).not.toContain("localStorage");
    fetchSpy.mockRestore();
  });
});
