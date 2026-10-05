from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_contract_ui_separates_prime_context_from_our_financial_contracts():
    module = (ROOT / "frontend/src/modules/contracts/ContractsModule.tsx").read_text(encoding="utf-8")
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    scheme = (ROOT / "frontend/src/modules/contracts/ContractScheme.tsx").read_text(encoding="utf-8")
    details = (ROOT / "frontend/src/modules/contracts/ContractDetails.tsx").read_text(encoding="utf-8")
    assert "Генподрядный договор — только контекст" in module
    assert "Наш субподрядный договор — доходы, ГПР, бюджет и ДДС" in module
    assert "Договор с субподрядчиком / субсубподрядчиком — расходы" in module
    assert "Выберите генподрядный договор" in module
    assert "Выберите непосредственный вышестоящий договор" in module
    assert "downstream_subcontract\"].includes" in module
    # The obsolete advanced list was merged into register/scheme details. Assert
    # the same hierarchy, role distinction and explicit finance guard at their
    # current owners, rather than requiring a second decorative list in App.
    assert "contract-register-count" in scheme
    assert "contract-register-open" in scheme
    assert 'data-kind={contract.contract_kind || "customer"}' in scheme
    assert "buildContractTree(contracts)" in scheme
    assert 'kind === "prime_reference"' in scheme
    assert 'kind === "revenue_subcontract"' in scheme
    assert 'kind === "downstream_subcontract"' in scheme
    assert "ГЕНПОДРЯД · КОНТЕКСТ" in app
    assert 'item.contract_kind !== "prime_reference" && <div className="contract-budget-proposal"' in app
    assert "Предложить бюджет по договору" in app
    assert "renderDetails={(contract, packageContent) => renderContractDetails" in app
    assert "contract-details" in details
    assert "retention_percent" in app
    assert "строк графика платежей" in app
