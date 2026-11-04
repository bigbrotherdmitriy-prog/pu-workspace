from decimal import Decimal, localcontext
import pytest
from app.provisional_policy import ProvisionalPolicy, derive_unit_price, get_policy
from app.estimate_import import _apply_math


def test_safe_defaults_and_unknown_config(monkeypatch):
    assert get_policy() == ProvisionalPolicy()
    monkeypatch.setenv("PU_NEGATIVE_STOCK_POLICY", "unrecognized")
    with pytest.raises(ValueError): get_policy()


@pytest.mark.parametrize("qty,total", [("2", "2.01"), ("3", "10.00")])
def test_ambiguous_derived_price_rejected_before_mutating_input(qty, total):
    line = {"qty": qty, "total": total}
    snapshot = dict(line)
    with pytest.raises(ValueError, match="Неоднозначная цена"):
        _apply_math(line, 1, [])
    assert line == snapshot


def test_explicit_opt_in_rounds_half_up_but_preserves_original_total(monkeypatch):
    monkeypatch.setenv("PU_ESTIMATE_ROUNDING_POLICY", "round_half_up")
    line = {"qty": "2", "total": "2.01"}
    _apply_math(line, 1, [])
    assert Decimal(line["unit_price"]) == Decimal("1.01")
    assert line["total"] == "2.01"


def test_safe_exact_derivation_is_independent_of_callers_decimal_context():
    with localcontext() as context:
        context.prec = 2
        assert derive_unit_price(Decimal("1000"), Decimal("1230.00")) == Decimal("1.23")


def test_explicit_price_is_not_silently_replaced_by_policy():
    line = {"qty": "2", "total": "2.01", "unit_price": "1.00"}
    _apply_math(line, 1, [])
    assert line["unit_price"] == "1.00" and line["total"] == "2.01"


def test_manual_patch_rejects_ambiguous_price_without_partial_change(db_session):
    from app.models.organization_contract import Organization
    from app.models.project import Project
    from app.models.estimates import EstimateLine
    from app import estimates
    db = db_session
    org = Organization(name="SYNTHETIC rounding")
    db.add(org); db.flush()
    project = Project(name="SYNTHETIC", organization_id=org.id)
    db.add(project); db.flush()
    created = estimates.create_estimate(db, project.id, "SYNTHETIC", {"lines": [{"name": "SYNTHETIC", "qty": "1", "total": "2.01"}]})
    row = db.query(EstimateLine).filter_by(estimate_id=created["id"]).one()
    with pytest.raises(ValueError, match="Неоднозначная цена"):
        estimates.update_line(db, project.id, row.id, {"qty": "2", "name": "must not change"})
    assert row.qty == Decimal("1") and row.name == "SYNTHETIC"


def test_legacy_ambiguous_total_remains_readable_but_is_not_silently_billed(db_session):
    from datetime import date
    from app.models.organization_contract import Organization
    from app.models.project import Project
    from app.models.estimates import Estimate,EstimateLine
    from app.models.work_log import WorkLogEntry
    from app import estimates
    db=db_session;org=Organization(name='SYNTHETIC legacy');db.add(org);db.flush()
    project=Project(name='SYNTHETIC legacy',organization_id=org.id);db.add(project);db.flush()
    estimate=Estimate(project_id=project.id,name='SYNTHETIC legacy',total=Decimal('2.01'));db.add(estimate);db.flush()
    line=EstimateLine(project_id=project.id,estimate_id=estimate.id,name='SYNTHETIC ambiguous',qty=Decimal('2'),total=Decimal('2.01'),unit_price=None)
    db.add(line);db.flush();db.add(WorkLogEntry(project_id=project.id,estimate_line_id=line.id,work_date=date(2026,10,5),work_name='SYNTHETIC',qty=1));db.commit()
    shown=estimates.get_estimate(db,project.id,estimate.id)
    assert shown['lines'][0]['unit_price'] is None
    preview=estimates.preview_act(db,project.id,estimate.id,date(2026,10,1),date(2026,10,28))
    assert preview['lines']==[] and preview['warnings']
    db.refresh(line);assert line.total==Decimal('2.01') and line.unit_price is None
