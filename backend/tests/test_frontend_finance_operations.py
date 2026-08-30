from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "frontend" / "src" / "App.tsx"
OPERATIONS = ROOT / "frontend" / "src" / "modules" / "finance" / "FinanceOperations.tsx"


def test_finance_operations_are_extracted_from_app_shell() -> None:
    app = APP.read_text(encoding="utf-8")
    operations = OPERATIONS.read_text(encoding="utf-8")

    assert 'from "./modules/finance/FinanceOperations"' in app
    assert "<FinanceOperations" in app
    assert "structured-import" not in app
    assert "finance-entry" not in app
    assert "finance-grid" not in app

    assert "structured-import" in operations
    assert "finance-entry" in operations
    assert "finance-grid" in operations
    assert "Создать пакет предложений" in operations
    assert "Подтвердить оплату" in operations


def test_finance_operations_preserve_human_approval_boundary() -> None:
    operations = OPERATIONS.read_text(encoding="utf-8")

    assert "Новая запись создаётся как предложение" in operations
    assert "не влияет на подтверждённый прогноз" in operations
    assert "onConfirmPayment" in operations
    assert "google_workspace" not in operations.lower()
