"""Synthetic legacy cash-flow links; never production IDs, names or amounts."""
import hashlib
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.dds_article_budget import ArticleBudgetApplyRequest, ArticleBudgetPreviewRequest, _snapshot, apply_article_budget, preview_article_budget
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, CostCategory, PaymentEvent
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


def service():
    from app import dds_budget_links
    return dds_budget_links


@pytest.fixture
def world(db_session, user_factory):
    return build_world(db_session, user_factory(is_admin=True))


def build_world(db, user):
    org = Organization(name="Synthetic link organization")
    db.add(org); db.flush()
    project = Project(name="Synthetic link project", organization_id=org.id, currency="RUB")
    db.add(project); db.flush()
    contract = Contract(project_id=project.id, number="TEST-LINK-001", title="Synthetic contract")
    category = CostCategory(organization_id=org.id, name="Материалы", normalized_name="материалы")
    document = Document(project_id=project.id, name="matrix.xlsx", current_version=1, source="local_upload")
    db.add_all([contract, category, document]); db.flush()
    content = "Статья\tГод\tянварь\tфевраль\tмарт\nМатериалы (Объект-Б)\t3\t1\t2\t0\nРаботы (Объект-Б)\t4\t4\t0\t0\n"
    version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db.add(version); db.flush()
    scope = dict(project_id=project.id, contract_id=contract.id, plan_year=2026, budget_revision=1)
    request = ArticleBudgetPreviewRequest(**scope, category_by_article={2: category.id, 3: category.id})
    proposal = preview_article_budget(document.id, request, db, user)
    result = apply_article_budget(document.id, ArticleBudgetApplyRequest(**request.model_dump(),
        preview_hash=proposal["preview_hash"], expected_document_version_id=version.id,
        expected_document_sha256=proposal["document_sha256"], idempotency_key="synthetic-budgets-1", owner_confirmed=True), db, user)
    lines = [db.get(BudgetLine, identifier) for identifier in result["created_budget_ids"]]
    rows = []
    for title, amount, month, coordinate in (("Материалы (Объект-Б)","0.98",1,"C2"),
                                              ("Материалы (Объект-Б)","2.00",2,"D2"),
                                              ("Работы (Объект-Б)","4.00",1,"C3")):
        row = CashFlowEntry(project_id=project.id, contract_id=contract.id, title=title, direction="outflow",
            planned_amount=Decimal(amount), planned_date=date(2026,month,28), currency="RUB", status="proposed",
            source_document_id=document.id, source_document_version_id=version.id,
            source_document_sha256=hashlib.sha256(content.encode()).hexdigest(), source_name=f"matrix.xlsx, {coordinate}")
        db.add(row); rows.append(row)
    db.commit()
    return SimpleNamespace(db=db,user=user,org=org,project=project,contract=contract,category=category,
                           document=document,version=version,rows=rows,lines=lines,scope=scope)


def preview(w):
    return service().preview_budget_links(w.document.id,service().BudgetLinkPreviewRequest(**w.scope),w.db,w.user)


def payload(w, proposal=None, **overrides):
    proposal = proposal or preview(w)
    values = dict(**w.scope, preview_hash=proposal["preview_hash"],
        expected_document_version_id=proposal["document_version_id"], expected_document_sha256=proposal["document_sha256"],
        expected_cash_versions={row["id"]:row["record_version"] for row in proposal["rows"]},
        owner_confirmed=True,idempotency_key="synthetic-links-1")
    values.update(overrides)
    return service().BudgetLinkApplyRequest(**values)


def apply(w, request=None):
    return service().apply_budget_links(w.document.id,request or payload(w),w.db,w.user)


def unchanged_business(before, row):
    after = _snapshot(row)
    for field in ("budget_line_id","record_version"):
        before = {key:value for key,value in before.items() if key != field}
        after.pop(field)
    assert before == after


def test_preview_is_read_only_and_shows_exact_links_and_manual_amounts(world):
    w = world; before = [_snapshot(row) for row in w.rows]
    audit_count = w.db.query(AuditLog).count()
    result = preview(w)
    assert result["conflicts"] == [] and result["apply_allowed"] is True
    assert result["expense_total"] == "6.98" and result["budget_total"] == "7.00" and result["difference"] == "-0.02"
    assert [row["budget_line_id"] for row in result["rows"]] == [w.lines[0].id,w.lines[0].id,w.lines[1].id]
    assert result["rows"][0]["amount"] == "0.98" and result["rows"][0]["current_budget_line_id"] is None
    assert [_snapshot(row) for row in w.rows] == before and w.db.query(AuditLog).count() == audit_count
    assert not w.db.new and not w.db.dirty and not w.db.deleted


def test_apply_changed_record_is_rejected_without_partial_links(world):
    w = world; request = payload(w)
    w.rows[1].planned_amount += Decimal("0.01"); w.rows[1].record_version += 1; w.db.flush()
    before = [_snapshot(row) for row in w.rows]
    with pytest.raises(HTTPException,match="CASH_FLOW_VERSION_MISMATCH"):
        apply(w,request)
    assert [_snapshot(row) for row in w.rows] == before
    assert all(row.budget_line_id is None for row in w.rows)
    assert w.db.query(service().DdsBudgetLinkOperation).count() == 0


def test_repeat_apply_is_idempotent_and_does_not_duplicate_audits(world):
    w = world; request = payload(w); first = apply(w,request); again = apply(w,request)
    assert first["operation_id"] == again["operation_id"] and again["replayed"] is True
    assert w.db.query(service().DdsBudgetLinkOperation).count() == 1
    assert w.db.query(AuditLog).filter_by(action="cash_flow_budget_link_applied").count() == 3
    assert [row.record_version for row in w.rows] == [2,2,2]


def test_whole_operation_undo_returns_all_links_to_empty(world):
    w = world; before = [_snapshot(row) for row in w.rows]; result = apply(w)
    assert [row.budget_line_id for row in w.rows] == [w.lines[0].id,w.lines[0].id,w.lines[1].id]
    undone = service().undo_budget_links(result["operation_id"],w.db,w.user)
    assert undone["undone"] is True and undone["active_link_count"] == 0
    assert all(row.budget_line_id is None and row.record_version == 3 for row in w.rows)
    for original,row in zip(before,w.rows): unchanged_business(original,row)
    again = service().undo_budget_links(result["operation_id"],w.db,w.user)
    assert again["replayed"] is True
    assert w.db.query(AuditLog).filter_by(action="cash_flow_budget_link_undone").count() == 3
    assert len(service().list_budget_link_operations(w.document.id,service().BudgetLinkPreviewRequest(**w.scope),w.db,w.user)) == 1


def test_apply_changes_only_budget_link_and_cas_metadata(world):
    w = world; before = [_snapshot(row) for row in w.rows]
    result = apply(w)
    assert result["link_count"] == 3 and result["linked_cash_flow_ids"] == [row.id for row in w.rows]
    for original,row in zip(before,w.rows):
        unchanged_business(original,row)
        assert row.entry_kind == "legacy_unclassified" and row.category is None and row.cost_category_id is None
    assert w.db.query(PaymentEvent).count() == 0


def test_explicit_owner_confirmation_is_required(world):
    with pytest.raises(HTTPException,match="OWNER_CONFIRMATION_REQUIRED"):
        apply(world,payload(world,owner_confirmed=False))
    assert all(row.budget_line_id is None for row in world.rows)


def test_new_key_cannot_reapply_an_old_preview(world):
    w = world; proposal = preview(w); apply(w,payload(w,proposal))
    with pytest.raises(HTTPException,match="CASH_FLOW_VERSION_MISMATCH|LINK_PREVIEW_STALE"):
        apply(w,payload(w,proposal,idempotency_key="synthetic-links-2"))
    assert w.db.query(service().DdsBudgetLinkOperation).count() == 1


def test_same_key_with_different_payload_conflicts(world):
    w = world; request = payload(w); apply(w,request)
    changed = request.model_copy(update={"preview_hash":"f"*64})
    with pytest.raises(HTTPException,match="IDEMPOTENCY_CONFLICT"):
        apply(w,changed)


@pytest.mark.parametrize("field",["planned_amount","planned_date","status","note"])
def test_even_unversioned_field_changes_invalidate_preview(world,field):
    w = world; request = payload(w)
    setattr(w.rows[1],field,{"planned_amount":Decimal("1.01"),"planned_date":date(2026,2,27),"status":"approved","note":"Synthetic edit"}[field])
    w.db.flush()
    with pytest.raises(HTTPException,match="LINK_PREVIEW_STALE|LINK_PREVIEW_CONFLICTS"):
        apply(w,request)
    assert all(row.budget_line_id is None for row in w.rows)


def test_changed_budget_and_source_block_apply(world):
    w = world; request = payload(w)
    w.lines[0].planned_amount += Decimal("0.01"); w.lines[0].record_version += 1; w.db.flush()
    with pytest.raises(HTTPException,match="LINK_PREVIEW_STALE"):
        apply(w,request)
    w.version.content += "changed"; w.db.flush()
    with pytest.raises(HTTPException,match="SOURCE_VERSION_MISMATCH"):
        apply(w,request)
    assert all(row.budget_line_id is None for row in w.rows)


def test_ambiguous_article_is_not_guessed(world):
    w = world; values = _snapshot(w.lines[0]); values.pop("id")
    for key in ("planned_amount","forecast_amount","committed_amount","actual_amount"): values[key] = Decimal(values[key])
    w.db.add(BudgetLine(**values)); w.db.flush()
    assert any(c["code"] == "ARTICLE_AMBIGUOUS" for c in preview(w)["conflicts"])
    assert preview(w)["apply_allowed"] is False


@pytest.mark.parametrize("field,value",[("currency","USD"),("budget_period",2027),("line_kind","contract_control")])
def test_matching_does_not_use_wrong_scope_or_contract_control(world,field,value):
    w = world; setattr(w.lines[0],field,value); w.db.flush()
    assert any(c["code"] == "BUDGET_LINE_REQUIRED" for c in preview(w)["conflicts"])


@pytest.mark.parametrize("field,value",[("source_document_sha256",None),("source_name","matrix.xlsx, Z999"),("entry_kind","invoice_commitment")])
def test_unproven_or_invoice_rows_cannot_use_the_legacy_link_path(world,field,value):
    w = world; setattr(w.rows[0],field,value); w.db.flush()
    assert preview(w)["apply_allowed"] is False


def test_undo_stale_row_rejects_the_entire_operation(world):
    w = world; result = apply(w)
    w.rows[1].note = "Changed later"; w.rows[1].record_version += 1; w.db.flush()
    links = [row.budget_line_id for row in w.rows]
    with pytest.raises(HTTPException,match="LINK_UNDO_STALE"):
        service().undo_budget_links(result["operation_id"],w.db,w.user)
    assert [row.budget_line_id for row in w.rows] == links
    assert w.db.query(AuditLog).filter_by(action="cash_flow_budget_link_undone").count() == 0


def test_audit_failure_rolls_back_the_whole_batch(world,monkeypatch):
    w = world; before = [_snapshot(row) for row in w.rows]
    original = service().AuditLog; calls = 0
    def fault(**values):
        nonlocal calls
        calls += 1
        if calls == 2: raise RuntimeError("synthetic-second-audit-fault")
        return original(**values)
    monkeypatch.setattr(service(),"AuditLog",fault)
    with pytest.raises(RuntimeError,match="synthetic-second-audit-fault"):
        apply(w)
    w.db.expire_all()
    assert [_snapshot(row) for row in w.rows] == before
    assert w.db.query(service().DdsBudgetLinkOperation).count() == 0


def test_unauthorized_preview_apply_undo_and_history_are_rejected(world,user_factory):
    w = world; outsider = user_factory(is_admin=False)
    with pytest.raises(HTTPException) as rejected:
        service().preview_budget_links(w.document.id,service().BudgetLinkPreviewRequest(**w.scope),w.db,outsider)
    assert rejected.value.status_code == 403
    with pytest.raises(HTTPException) as rejected:
        service().apply_budget_links(w.document.id,payload(w),w.db,outsider)
    assert rejected.value.status_code == 403
    operation = apply(w)
    with pytest.raises(HTTPException) as rejected:
        service().undo_budget_links(operation["operation_id"],w.db,outsider)
    assert rejected.value.status_code == 403
    with pytest.raises(HTTPException) as rejected:
        service().list_budget_link_operations(w.document.id,service().BudgetLinkPreviewRequest(**w.scope),w.db,outsider)
    assert rejected.value.status_code == 403


def test_replay_of_undone_operation_does_not_reapply_links(world):
    w = world; request = payload(w); operation = apply(w,request)
    service().undo_budget_links(operation["operation_id"],w.db,w.user)
    result = apply(w,request)
    assert result["replayed"] is True and result["undone"] is True and result["active_link_count"] == 0
    assert all(row.budget_line_id is None for row in w.rows)
