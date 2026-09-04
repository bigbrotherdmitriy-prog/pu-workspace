from decimal import Decimal
from types import SimpleNamespace

from app.api.contract_package import _financial_issues, router


def test_contract_package_routes_are_available():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/contracts/{contract_id}/applications" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/documents" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/analyze-package" in paths


def test_package_check_reports_financial_mismatch_without_confirming_payment():
    contract = SimpleNamespace(amount=Decimal("100"), advance_amount=None, retention_percent=None)
    document = SimpleNamespace(id=7, name="Приложение Цена.docx")
    issues = _financial_issues(contract, document, "Цена договора 120 руб.")
    assert issues[0]["field"] == "amount"
    assert issues[0]["contract_value"] == "100"
    assert issues[0]["document_value"] == "120"
