from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_contract_ui_separates_prime_context_from_our_financial_contracts():
    module = (ROOT / "frontend/src/modules/contracts/ContractsModule.tsx").read_text(encoding="utf-8")
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "Генподрядный договор — только контекст" in module
    assert "Наш субподрядный договор — доходы, ГПР, бюджет и ДДС" in module
    assert "Договор с субподрядчиком / субсубподрядчиком — расходы" in module
    assert "Выберите генподрядный договор" in module
    assert "Выберите непосредственный вышестоящий договор" in module
    assert "downstream_subcontract\"].includes" in module
    assert "contract-project-root" in app
    assert "buildContractTree(contracts, query)" in app
    assert "retention_percent" in app
    assert "строк графика платежей" in app
