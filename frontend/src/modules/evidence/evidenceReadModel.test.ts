import { describe, expect, it } from "vitest";
import { EVIDENCE_FRAGMENT_SCHEMA_VERSION, toEvidenceFragmentViewModel } from "./evidenceReadModel";

function validInput() {
  return {
    schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION as string,
    capabilities: { metadata: "allow", fragment: "allow", archival_fragment: "deny" },
    policy: { known: true, version: "policy-7" as string | null },
    evidence: { id: "evidence-16", revision: 1, source_id: "source-13", source_version_id: "version-15", status: "verified" },
    source: {
      id: "source-13", record_version: 4, current_source_version_id: "version-15",
      version_state: "current", freshness: "fresh", availability: "available",
      provider: "google-workspace", account: "project-mailbox", namespace: "mailbox-primary", origin_project: "Альфа",
    },
    source_version: { id: "version-15", revision: 1, source_id: "source-13" },
    locator: { kind: "page", page: 2 } as Record<string, unknown>,
    fragment: { media_type: "text/plain", excerpt: "Оплатить до 10 сентября." },
    extracted_fact: "Срок: 10.09.2026",
    ai_conclusion: "Возможно, нужна внутренняя задача.",
    extractor: {
      name: "local-ocr", version: "2", method: "ocr",
      model_provider: "local", model_id: "classifier", model_version: "1", prompt_version: "prompt-3",
    },
    confidence: { value: 0.923, kind: "calibrated", calibration_ref: "calibration-2" },
    assessment: { verification: "verified", reviewer: "Мария" as string | null, reviewed_at: "2026-09-03T09:01:00Z" as string | null, record_version: 3 },
    historical: false,
    reason_code: null as string | null,
  };
}

describe("toEvidenceFragmentViewModel", () => {
  it("accepts verified current readable evidence and preserves exact pins", () => {
    const model = toEvidenceFragmentViewModel(validInput());
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.fragmentVisible).toBe(true);
    expect(model.statusLabel).toBe("Проверено");
    expect(model.pins).toEqual({ evidence: "evidence-16/r1", source: "source-13/r4", sourceVersion: "version-15/r1" });
    expect(model.fragment?.excerpt).toContain("10 сентября");
  });

  it("allows an explicitly readable unverified fragment for human review", () => {
    const input = validInput();
    input.evidence.status = "unverified";
    input.assessment = { verification: "unverified", reviewer: null, reviewed_at: null, record_version: 1 };
    const model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.fragmentVisible).toBe(true);
    expect(model.assessment.label).toBe("Не проверено");
  });

  it.each([
    ["stale", { freshness: "stale" }, "source_revision_changed"],
    ["unavailable", { availability: "unavailable" }, "provider_unavailable"],
    ["unavailable", { availability: "revoked" }, "access_revoked"],
    ["unavailable", { availability: "expired" }, "fragment_expired"],
    ["unavailable", { availability: "purged" }, "fragment_purged"],
  ])("hides fragment for %s evidence", (status, sourcePatch, reason) => {
    const input = validInput();
    input.evidence.status = status;
    Object.assign(input.source, sourcePatch);
    input.reason_code = reason;
    const model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.fragmentVisible).toBe(false);
    expect(model.fragment).toBeNull();
    expect(model.extractedFact).toBeNull();
    expect(model.aiConclusion).toBeNull();
  });

  it("hides all existence metadata when metadata capability is denied", () => {
    const input = validInput();
    input.capabilities.metadata = "deny";
    expect(toEvidenceFragmentViewModel(input)).toMatchObject({ kind: "hidden", metadataVisible: false, fragmentVisible: false });
  });

  it.each([
    ["unknown schema", (input: ReturnType<typeof validInput>) => { input.schema_version = "future-schema"; }],
    ["unknown reason", (input: ReturnType<typeof validInput>) => { input.reason_code = "raw_backend_exception"; }],
    ["unknown metadata capability", (input: ReturnType<typeof validInput>) => { input.capabilities.metadata = "future"; }],
  ])("fails closed for %s", (_label, change) => {
    const input = validInput();
    change(input);
    const model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("hidden");
    expect(model.reasonLabel).toBe("Не удалось безопасно проверить доказательство.");
  });

  it.each([
    ["unknown fragment capability", (input: ReturnType<typeof validInput>) => { input.capabilities.fragment = "unknown"; }],
    ["unknown archival capability", (input: ReturnType<typeof validInput>) => { input.capabilities.archival_fragment = "unknown"; }],
    ["unknown policy", (input: ReturnType<typeof validInput>) => { input.policy = { known: false, version: null }; }],
    ["unknown version", (input: ReturnType<typeof validInput>) => { input.source.version_state = "unknown"; }],
  ])("keeps allowed metadata but hides content for %s", (_label, change) => {
    const input = validInput();
    change(input);
    const model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.fragmentVisible).toBe(false);
    expect(model.fragment).toBeNull();
  });

  it.each([
    ["evidence/source mismatch", (input: ReturnType<typeof validInput>) => { input.evidence.source_id = "other-source"; }],
    ["version/source mismatch", (input: ReturnType<typeof validInput>) => { input.source_version.source_id = "other-source"; }],
    ["evidence/version mismatch", (input: ReturnType<typeof validInput>) => { input.evidence.source_version_id = "other-version"; }],
    ["current pointer mismatch", (input: ReturnType<typeof validInput>) => { input.source.current_source_version_id = "other-version"; }],
    ["malformed revision", (input: ReturnType<typeof validInput>) => { input.evidence.revision = 0; }],
    ["contradictory assessment", (input: ReturnType<typeof validInput>) => { input.assessment.verification = "unverified"; }],
  ])("fails closed for malformed or mismatched pins: %s", (_label, change) => {
    const input = validInput();
    change(input);
    expect(toEvidenceFragmentViewModel(input).kind).toBe("hidden");
  });

  it("shows historical evidence only with explicit archival authorization", () => {
    const input = validInput();
    input.historical = true;
    input.source.version_state = "historical";
    input.source.current_source_version_id = "version-new";
    let model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.fragmentVisible).toBe(false);
    input.capabilities.archival_fragment = "allow";
    model = toEvidenceFragmentViewModel(input);
    expect(model.kind).toBe("evidence");
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.historical).toBe(true);
    expect(model.fragmentVisible).toBe(true);
  });

  it("formats confidence as extraction quality, not truth", () => {
    const model = toEvidenceFragmentViewModel(validInput());
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.extraction.confidence).toMatch(/Оценка извлечения: 92,3\s?%/);
    expect(model.extraction.calibration).toBe("calibration-2");
  });

  it.each([
    [{ kind: "whole_object", reason_code: "content_read_forbidden" }, "whole_object", false],
    [{ kind: "page", page: 3 }, "page", true],
    [{ kind: "page_bbox", page: 1, coordinate_space: "pixels", box: [10, 20, 30, 40], extent: [100, 100], representation_id: "raster-1", precise_navigation: false }, "page_bbox", false],
    [{ kind: "section_clause", section_path: ["Договор", "Сроки"], clause_label: "2.1", anchor: "clause-2-1" }, "section_clause", true],
    [{ kind: "sheet_cell", sheet_key: "sheet-1", sheet_name: "ДДС", range_a1: "B2:C3", value_kind: "displayed_value" }, "sheet_cell", true],
    [{ kind: "message", message_external_id: "message-1", part: "body", char_range: [5, 20] }, "message", true],
    [{ kind: "attachment", message_external_id: "message-1", attachment_external_id: "attachment-1", attachment_source_reference_id: "source-attachment" }, "attachment", true],
    [{ kind: "record", record_key: "row-7", field_path: ["payment", "amount"] }, "record", true],
  ])("validates locator variant %s", (locator, kind, preciseNavigation) => {
    const input = validInput();
    input.locator = locator;
    const model = toEvidenceFragmentViewModel(input);
    if (model.kind !== "evidence") throw new Error("expected visible metadata");
    expect(model.locator.kind).toBe(kind);
    expect(model.locator.preciseNavigation).toBe(preciseNavigation);
  });

  it("fails closed for an invalid locator geometry", () => {
    const input = validInput();
    input.locator = { kind: "page_bbox", page: 1, coordinate_space: "pixels", box: [90, 90, 20, 20], extent: [100, 100], representation_id: "raster-1", precise_navigation: true };
    expect(toEvidenceFragmentViewModel(input).kind).toBe("hidden");
  });

  it("fails closed for a reason that contradicts readable policy state", () => {
    const input = validInput();
    input.reason_code = "policy_denied";
    expect(toEvidenceFragmentViewModel(input)).toMatchObject({ kind: "hidden", reasonLabel: "Не удалось безопасно проверить доказательство." });
  });
});
