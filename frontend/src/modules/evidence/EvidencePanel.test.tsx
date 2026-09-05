import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvidencePanel } from "./EvidencePanel";
import { EVIDENCE_FRAGMENT_SCHEMA_VERSION } from "./evidenceReadModel";

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "test-request" },
  }));
}

function readable() {
  return {
    schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION,
    state: "readable",
    status: "verified",
    version_state: "historical",
    freshness: "fresh",
    availability: "available",
    valid_until: "2026-09-03T09:03:00Z",
    evidence: { id: "evidence-1", revision: 1, source_id: "source-1", source_version_id: "version-1" },
    source: { id: "source-1", record_version: 2, current_source_version_id: "version-2",
      provider: "synthetic", account: "mailbox-1", namespace: "inbox", origin_project: "project-4" },
    source_version: { id: "version-1", revision: 1, source_id: "source-1" },
    locator: { kind: "page", page: 2 },
    fragment: { media_type: "text/plain", excerpt: "Exact historical fragment" },
    extracted_fact: null,
    ai_conclusion: null,
    extractor: { name: "fixture", version: "1", method: null, model_provider: null,
      model_id: null, model_version: null, prompt_version: null },
    confidence: { value: null, kind: "unknown", calibration_ref: null },
    assessment: { verification: "verified", reviewer: "user-3",
      reviewed_at: "2026-09-03T09:00:00Z", record_version: 2 },
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EvidencePanel", () => {
  it("loads exact evidence only while detail is open and requests no browser cache", async () => {
    const fetch = vi.fn(() => response(readable()));
    vi.stubGlobal("fetch", fetch);
    const { rerender } = render(<EvidencePanel active={false} evidenceRefs={[{ id: "evidence-1", revision: 1 }]} />);
    expect(fetch).not.toHaveBeenCalled();

    rerender(<EvidencePanel active evidenceRefs={[{ id: "evidence-1", revision: 1 }]} />);
    expect(await screen.findByText("Exact historical fragment")).toBeInTheDocument();
    expect(screen.getByText(/Историческое доказательство/)).toBeInTheDocument();
    expect(screen.getByText(/03.09.2026/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "/api/v54/evidence/evidence-1/fragment?revision=1",
      expect.objectContaining({ cache: "no-store", credentials: "same-origin" }),
    );
  });

  it("renders the same content-free unavailable card for a denied response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response({ detail: "Evidence unavailable" }, 404)));
    render(<EvidencePanel active evidenceRefs={[{ id: "evidence-1", revision: 1 }]} />);

    expect(await screen.findByText("Доказательство недоступно")).toBeInTheDocument();
    expect(screen.queryByText("Evidence unavailable")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("evidence-1");
  });

  it("drops fragment state when the detail closes", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(readable())));
    const { rerender } = render(<EvidencePanel active evidenceRefs={[{ id: "evidence-1", revision: 1 }]} />);
    expect(await screen.findByText("Exact historical fragment")).toBeInTheDocument();
    rerender(<EvidencePanel active={false} evidenceRefs={[{ id: "evidence-1", revision: 1 }]} />);
    await waitFor(() => expect(screen.queryByText("Exact historical fragment")).not.toBeInTheDocument());
  });
});
