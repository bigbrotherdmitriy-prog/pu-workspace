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
    assert "Начать разбор пакета" in module
    assert "candidates.slice(0, 12)" not in module
    assert "Минимальная уверенность" in module
    assert "onUseCandidate" in module
    assert "google_workspace" not in module.casefold()
