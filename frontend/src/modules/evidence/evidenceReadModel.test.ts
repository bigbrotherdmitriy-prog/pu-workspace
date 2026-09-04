import { describe, expect, it } from "vitest";
import { EVIDENCE_FRAGMENT_SCHEMA_VERSION, toEvidenceFragmentViewModel } from "./evidenceReadModel";

type MutableRecord = Record<string, unknown>;

function readableInput() {
  return {
    schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION as string,
    state: "readable",
    status: "verified",
    version_state: "current",
    freshness: "fresh",
    availability: "available",
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
    locator: { kind: "page", page: 2 } as MutableRecord,
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

function unavailableInput(reason = "access_revoked") {
  return {
    schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION as string,
    state: "unavailable",
    status: "unavailable",
    reason_code: reason,
  };
}

describe("strict server evidence DTO", () => {
  it("accepts a server-authorized current readable variant", () => {
    const model = toEvidenceFragmentViewModel(readableInput());
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected readable evidence");
    expect(model.fragment.excerpt).toContain("10 сентября");
    expect(model.pins).toEqual({
      evidence: "evidence-16/r1",
      source: "source-13/r4",
      sourceVersion: "version-15/r1",
    });
    expect(model.historical).toBe(false);
  });

  it("accepts an explicitly readable unverified variant for human review", () => {
    const input = readableInput();
    input.status = "unverified";
    input.assessment = { verification: "unverified", reviewer: null, reviewed_at: null, record_version: 4 };
    const model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected readable evidence");
    expect(model.assessment.verification).toBe("unverified");
  });

  it.each(["access_revoked", "policy_denied", "source_revision_changed", "fragment_expired", "resource_unavailable"])(
    "accepts a minimal unavailable variant without content for %s",
    (reason) => {
      const input = unavailableInput(reason);
      expect(Object.keys(input).sort()).toEqual(["reason_code", "schema_version", "state", "status"]);
      expect(input).not.toHaveProperty("fragment");
      expect(input).not.toHaveProperty("extracted_fact");
      expect(input).not.toHaveProperty("ai_conclusion");
      expect(input).not.toHaveProperty("source");
      expect(toEvidenceFragmentViewModel(input)).toMatchObject({
        kind: "hidden",
        metadataVisible: false,
        fragmentVisible: false,
      });
    },
  );

  it.each(["fragment", "extracted_fact", "ai_conclusion", "source", "provider_locator", "account"])(
    "rejects unavailable payload carrying forbidden field %s",
    (field) => {
      const input: MutableRecord = { ...unavailableInput(), [field]: "SENSITIVE-BYTES" };
      const model = toEvidenceFragmentViewModel(input);
      expect(model).toMatchObject({ kind: "hidden", reasonLabel: "Не удалось безопасно проверить доказательство." });
      expect(JSON.stringify(model)).not.toContain("SENSITIVE-BYTES");
    },
  );

  it("does not accept client-side capability or archival authorization hints", () => {
    const input: MutableRecord = {
      ...readableInput(),
      capabilities: { fragment: "allow" },
      archival_fragment: "allow",
    };
    expect(toEvidenceFragmentViewModel(input).kind).toBe("hidden");
  });

  it.each(["unknown", "stale"])("fails closed for contradictory verified %s freshness", (freshness) => {
    const input = readableInput();
    input.freshness = freshness;
    expect(toEvidenceFragmentViewModel(input)).toMatchObject({ kind: "hidden", metadataVisible: false });
  });

  it.each(["unknown", "revoked", "expired", "purged", "unavailable"])(
    "fails closed when a readable variant reports %s availability",
    (availability) => {
      const input = readableInput();
      input.availability = availability;
      expect(toEvidenceFragmentViewModel(input).kind).toBe("hidden");
    },
  );

  it("renders historical content only when the server itself returns the readable variant", () => {
    const input = readableInput();
    input.version_state = "historical";
    input.source.current_source_version_id = "version-current";
    const model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected readable evidence");
    expect(model.historical).toBe(true);
    expect(model.fragmentVisible).toBe(true);

    const denied = unavailableInput("source_revision_changed");
    expect(toEvidenceFragmentViewModel(denied)).toMatchObject({ kind: "hidden", fragmentVisible: false });
  });

  it.each([
    ["evidence source", (input: ReturnType<typeof readableInput>) => { input.evidence.source_id = "other"; }],
    ["source version", (input: ReturnType<typeof readableInput>) => { input.source_version.source_id = "other"; }],
    ["evidence version", (input: ReturnType<typeof readableInput>) => { input.evidence.source_version_id = "other"; }],
    ["current pointer", (input: ReturnType<typeof readableInput>) => { input.source.current_source_version_id = "other"; }],
    ["assessment", (input: ReturnType<typeof readableInput>) => { input.assessment.verification = "unverified"; }],
  ])("rejects contradictory binding: %s", (_label, mutate) => {
    const input = readableInput();
    mutate(input);
    expect(toEvidenceFragmentViewModel(input).kind).toBe("hidden");
  });

  it("rejects sensitive nested extras such as provider locators", () => {
    const input = readableInput();
    const source: MutableRecord = input.source;
    source.provider_locator = "https://provider.example/object?token=secret";
    const model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("hidden");
    expect(JSON.stringify(model)).not.toContain("provider.example");
  });

  it.each([
    [{ kind: "whole_object", reason_code: "granularity_unavailable" }, "whole_object", false],
    [{ kind: "page", page: 3 }, "page", true],
    [{
      kind: "page_bbox",
      page: 1,
      coordinate_space: "representation",
      units: "pixels",
      box: [10, 20, 30, 40],
      extent: [100, 100],
      representation_id: "raster-1",
      precise_navigation: false,
    }, "page_bbox", false],
    [{ kind: "section_clause", section_path: ["Договор"], clause_label: "2.1", anchor: "clause-2-1" }, "section_clause", true],
    [{ kind: "sheet_cell", sheet_key: "sheet-1", sheet_name: "ДДС", range_a1: "B2:C3", value_kind: "displayed_value" }, "sheet_cell", true],
    [{ kind: "message", message_external_id: "message-1", part: "body", char_range: [5, 20] }, "message", true],
    [{ kind: "attachment", message_external_id: "message-1", attachment_external_id: "attachment-1", attachment_source_reference_id: "source-attachment" }, "attachment", true],
    [{ kind: "record", record_key: "row-7", field_path: ["payment", "amount"] }, "record", true],
  ])("parses exact locator %s", (locator, kind, precise) => {
    const input = readableInput();
    input.locator = locator;
    const model = toEvidenceFragmentViewModel(input);
    if (model.kind !== "evidence") throw new Error("expected readable evidence");
    expect(model.locator.kind).toBe(kind);
    expect(model.locator.preciseNavigation).toBe(precise);
  });

  it("rejects locator extras and invalid geometry", () => {
    const input = readableInput();
    input.locator = {
      kind: "page_bbox",
      page: 1,
      coordinate_space: "original",
      units: "pixels",
      box: [90, 90, 20, 20],
      extent: [100, 100],
      representation_id: "raster-1",
      precise_navigation: true,
      signed_url: "https://provider.example/secret",
    };
    expect(toEvidenceFragmentViewModel(input).kind).toBe("hidden");
  });
});
