import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvidenceFragmentCard } from "./EvidenceFragmentCard";
import { EVIDENCE_FRAGMENT_SCHEMA_VERSION } from "./evidenceReadModel";

function readableInput() {
  return {
    schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION as string,
    state: "readable",
    status: "verified",
    version_state: "current",
    freshness: "fresh",
    availability: "available",
    valid_until: "2026-09-03T09:03:00Z",
    evidence: { id: "evidence-16", revision: 1, source_id: "source-13", source_version_id: "version-15" },
    source: {
      id: "source-13",
      record_version: 4,
      current_source_version_id: "version-15",
      provider: "synthetic-provider",
      account: "synthetic-account",
      namespace: "synthetic-mailbox",
      origin_project: "Синтетический проект",
    },
    source_version: { id: "version-15", revision: 1, source_id: "source-13" },
    locator: { kind: "page", page: 2 } as Record<string, unknown>,
    fragment: { media_type: "text/plain", excerpt: "Синтетический срок: 10 сентября." },
    extracted_fact: "Срок: 10.09.2026",
    ai_conclusion: "Требуется проверка человеком.",
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
      reviewer: "Синтетический проверяющий" as string | null,
      reviewed_at: "2026-09-03T09:01:00Z" as string | null,
      record_version: 3,
    },
  };
}

function unavailableInput() {
  return {
    schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION as string,
    state: "unavailable",
    status: "unavailable",
    reason_code: "access_revoked",
  };
}

afterEach(cleanup);

describe("EvidenceFragmentCard", () => {
  it("renders a server-readable evidence fragment", () => {
    render(<EvidenceFragmentCard input={readableInput()} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("status")).toHaveAttribute("aria-atomic", "true");
    expect(screen.getByRole("heading", { name: "Основание вывода" })).toBeInTheDocument();
    expect(screen.getByText("Синтетический срок: 10 сентября.")).toBeInTheDocument();
    expect(screen.getByText("evidence-16/r1")).toBeInTheDocument();
  });

  it("announces a change from readable to unavailable and removes prior content", () => {
    const { container, rerender } = render(<EvidenceFragmentCard input={readableInput()} />);
    expect(container).toHaveTextContent("Синтетический срок: 10 сентября.");
    rerender(<EvidenceFragmentCard input={unavailableInput()} />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("Доказательство недоступно");
    expect(container).not.toHaveTextContent("Синтетический срок");
    expect(container).not.toHaveTextContent("evidence-16");
    expect(container).not.toHaveTextContent("synthetic-account");
  });

  it("does not retain bytes or metadata when denied payload is malformed with extras", () => {
    const input = {
      ...unavailableInput(),
      fragment: { excerpt: "SENSITIVE-BYTES" },
      extracted_fact: "SENSITIVE-FACT",
      ai_conclusion: "SENSITIVE-AI",
      source: {
        account: "SENSITIVE-ACCOUNT",
        provider_locator: "https://provider.example/object?token=secret",
      },
    };
    const { container } = render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByText("Не удалось безопасно проверить доказательство.")).toBeInTheDocument();
    expect(container.outerHTML).not.toContain("SENSITIVE");
    expect(container.outerHTML).not.toContain("provider.example");
  });

  it("renders historical warning only for a readable historical response", () => {
    const input = readableInput();
    input.version_state = "historical";
    input.source.current_source_version_id = "version-current";
    render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByText(/Историческое доказательство/)).toBeInTheDocument();
    expect(screen.getByText("Синтетический срок: 10 сентября.")).toBeInTheDocument();
  });

  it("fails closed for contradictory verified stale input", () => {
    const input = readableInput();
    input.freshness = "stale";
    const { container } = render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByRole("status")).toHaveTextContent("Доказательство недоступно");
    expect(container).not.toHaveTextContent("Синтетический срок");
  });

  it("renders malicious source text as escaped inert text", () => {
    const input = readableInput();
    input.fragment.excerpt = '<img src=x onerror="globalThis.pwned=true"><script>alert(1)</script>';
    const { container } = render(<EvidenceFragmentCard input={input} />);
    expect(screen.getByText(input.fragment.excerpt)).toBeInTheDocument();
    expect(container.querySelector("img, script")).toBeNull();
  });

  it("does not fetch, use storage, or expose mutation controls", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const storageSpy = vi.spyOn(Storage.prototype, "setItem");
    const { container } = render(<EvidenceFragmentCard input={readableInput()} />);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(storageSpy).not.toHaveBeenCalled();
    expect(container.querySelector("button, input, select, textarea, form")).toBeNull();
    expect(container.innerHTML).not.toContain("dangerouslySetInnerHTML");
    fetchSpy.mockRestore();
    storageSpy.mockRestore();
  });
});
