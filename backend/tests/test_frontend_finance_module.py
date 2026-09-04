from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_finance_overview_is_extracted_from_app_monolith():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend" / "src" / "modules" / "finance" / "FinanceModule.tsx").read_text(encoding="utf-8")

    assert 'from "./modules/finance/FinanceModule"' in app
    assert "<FinanceModule" in app
    assert "finance-contract-chain" in module
    assert "finance-chain-guide" in module
    assert "finance-document-assistant" in module
    assert "finance-contract-chain" not in app
    assert "finance-document-assistant" not in app


def test_finance_module_preserves_contract_first_and_human_review_flow():
    module = (ROOT / "frontend" / "src" / "modules" / "finance" / "FinanceModule.tsx").read_text(encoding="utf-8")

    assert "Договор → ГПР → бюджет → ДДС → акты" in module
    assert "Оригинал не меняется" in module
    assert "Проверить и использовать" in module
    assert "Выбрать найденные" in module
    assert "Открыть первый выбранный" in module
    assert "последовательной проверки" in module
    assert "candidates.slice(0, 12)" not in module
    assert "Минимальная уверенность" in module
    assert "onUseCandidate" in module
    assert "google_workspace" not in module.casefold()


def test_dds_workspace_mirrors_reference_workbook_views():
    module = (ROOT / "frontend" / "src" / "modules" / "finance" / "DdsWorkspace.tsx").read_text(encoding="utf-8")

    assert "ДДС по месяцам" in module
    assert "Календарь (вид ГПР)" in module
    assert "Детализация" in module
    assert "Сводка" in module
    assert "object_name" in module
    assert "category" in module


def test_structured_preview_shows_exact_source_coordinate():
    module = (ROOT / "frontend" / "src" / "modules" / "finance" / "FinanceOperations.tsx").read_text(encoding="utf-8")
    types = (ROOT / "frontend" / "src" / "modules" / "finance" / "types.ts").read_text(encoding="utf-8")

    assert "row.source_coordinate" in module
    assert "source_sheet?: string" in types
    assert "source_coordinate: string" in types


def test_gpr_workspace_exposes_project_style_planning_without_microsoft_branding():
    module = (ROOT / "frontend" / "src" / "modules" / "finance" / "GprWorkspace.tsx").read_text(encoding="utf-8")

    assert "График работ" in module
    assert "Критический путь" in module
    assert "Предш." in module
    assert "Отступ" in module
    assert "Веха" in module
    assert "Microsoft Project" not in module
