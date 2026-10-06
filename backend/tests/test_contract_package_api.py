from decimal import Decimal
from types import SimpleNamespace

from app.api.contract_package import _apply_financial_terms_from_document, router


def test_contract_package_routes_are_available():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/contracts/{contract_id}/applications" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/documents" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/analyze-package" in paths


def _synthetic_contract(**overrides):
    base = dict(amount=None, advance_amount=None, retention_percent=None,
               signed_at=None, warranty_until=None, vat_mode=None, vat_rate=None,
               performed_from=None, performed_to=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_package_check_reports_financial_mismatch_without_confirming_payment():
    contract = _synthetic_contract(amount=Decimal("100"))
    document = SimpleNamespace(id=7, name="Приложение Цена.docx")
    result = _apply_financial_terms_from_document(contract, document, "Цена договора 120 руб.")
    assert result["issues"][0]["field"] == "amount"
    assert result["issues"][0]["contract_value"] == "100"
    assert result["issues"][0]["document_value"] == "120"
    assert contract.amount == Decimal("100")


def test_package_check_fills_an_empty_field_and_cites_the_document():
    contract = _synthetic_contract()
    document = SimpleNamespace(id=9, name="Доп.соглашение.docx")
    result = _apply_financial_terms_from_document(contract, document, "Цена договора 120 руб.")
    assert contract.amount == Decimal("120")
    assert result["applied"] == [{"field": "amount", "document_id": 9, "document_name": "Доп.соглашение.docx"}]
