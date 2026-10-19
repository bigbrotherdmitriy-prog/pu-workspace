import { describe, expect, it } from "vitest";

import {
  ERROR_CODE_LABELS,
  humanizeEntity,
  humanizeErrorText,
  humanizeStatus,
} from "./humanize";

describe("humanizeErrorText", () => {
  it("maps known codes to Russian labels", () => {
    expect(humanizeErrorText("CATEGORY_CONFIRMATION_REQUIRED")).toBe(
      "Подтвердите категорию затрат для статьи",
    );
    expect(humanizeErrorText("SOURCE_VERSION_MISMATCH")).toBe(
      "Документ изменился после предпросмотра — обновите предпросмотр",
    );
    expect(humanizeErrorText("CONFIRMED")).toBe(ERROR_CODE_LABELS.CONFIRMED);
    expect(humanizeErrorText("NOT_APPLIED")).toBe("Не применено");
  });

  it("uses a fallback phrase for unknown codes", () => {
    expect(humanizeErrorText("SOME_UNKNOWN_CODE")).toBe("Операция не выполнена");
  });

  it("keeps a Cyrillic remainder after the code", () => {
    expect(humanizeErrorText("SOURCE_VERSION_MISMATCH: версия документа изменилась")).toBe(
      "Документ изменился после предпросмотра — обновите предпросмотр: версия документа изменилась",
    );
  });

  it("returns plain Russian text unchanged", () => {
    expect(humanizeErrorText("Что-то пошло не так")).toBe("Что-то пошло не так");
  });
});

describe("humanizeStatus", () => {
  it("translates known statuses", () => {
    expect(humanizeStatus("waiting_confirmation")).toBe("Ждёт подтверждения");
  });

  it("replaces underscores for unknown statuses", () => {
    expect(humanizeStatus("foo_bar")).toBe("foo bar");
  });

  it("returns empty string for empty values", () => {
    expect(humanizeStatus(undefined)).toBe("");
    expect(humanizeStatus(null)).toBe("");
  });
});

describe("humanizeEntity", () => {
  it("translates known entity types", () => {
    expect(humanizeEntity("cash_flow_entry")).toBe("Строка ДДС");
    expect(humanizeEntity("task")).toBe("Задача");
  });
});
