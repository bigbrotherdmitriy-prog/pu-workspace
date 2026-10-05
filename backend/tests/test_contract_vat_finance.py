"""Contract VAT provenance for synthetic finance records; gross money is untouched."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import execution_finance as api
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import (
    AcceptanceAct, BudgetLine, CashFlowEntry, CostCategory, DdsArticleBudgetOperation,
    InvoiceExtractionProposal, PaymentEvent, ScheduleBaseline, ScheduleItem,
)
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


@pytest.fixture
def vat_world(db_session, user_factory):
    db = db_session
    user = user_factory(is_admin=True)
    org = Organization(name="Synthetic VAT organization")
    db.add(org); db.flush()
    project = Project(name="Synthetic VAT project", organization_id=org.id, currency="RUB")
    db.add(project); db.flush()
    contract = Contract(project_id=project.id, number="VAT-TEST", title="Synthetic VAT contract")
    contract.vat_mode, contract.vat_rate = "rate", Decimal("22.00")
    category = CostCategory(organization_id=org.id, name="Материалы", normalized_name="материалы")
    db.add_all([contract, category]); db.flush()
    baseline = ScheduleBaseline(project_id=project.id, contract_id=contract.id, name="Synthetic schedule",
                                created_by_user_id=user.id, version=1)
    budget = BudgetLine(project_id=project.id, contract_id=contract.id, category=category.name,
        description="Synthetic budget", planned_amount=Decimal("122.00"), currency="RUB", status="approved")
    db.add_all([baseline, budget]); db.flush()
    stage = ScheduleItem(project_id=project.id, baseline_id=baseline.id, title="Synthetic stage")
    db.add(stage); db.commit()
    return SimpleNamespace(db=db, user=user, project=project, contract=contract, category=category,
                           baseline=baseline, budget=budget, stage=stage)


def snapshot(contract, mode="rate", rate="22.00"):
    return dict(schema_version=1, mode=mode, rate=rate, source_contract_id=contract.id,
                source_contract_record_version=contract.record_version)


def cash(w, **values):
    data = dict(project_id=w.project.id, contract_id=w.contract.id, direction="inflow", title="Synthetic cash",
        planned_date=date(2026, 10, 5), planned_amount=Decimal("122.00"), currency="RUB")
    data.update(values)
    row = CashFlowEntry(**data)
    w.db.add(row); w.db.commit()
    return row


def document(w, content, name="synthetic.tsv"):
    row = Document(project_id=w.project.id, name=name, current_version=1, source="local_upload")
    w.db.add(row); w.db.flush()
    version = DocumentVersion(document_id=row.id, version_number=1, content=content)
    w.db.add(version); w.db.flush()
    return row, version, hashlib.sha256(content.encode()).hexdigest()


@pytest.mark.parametrize("mode,rate", [("unspecified", None), ("none", None), ("rate", "0.00"), ("rate", "22.00")])
def test_contract_snapshot_distinguishes_known_modes(vat_world, mode, rate):
    from app.contract_vat import contract_vat_snapshot
    w = vat_world
    w.contract.vat_mode, w.contract.vat_rate = mode, Decimal(rate) if rate is not None else None
    assert contract_vat_snapshot(w.contract) == snapshot(w.contract, mode, rate)
    assert contract_vat_snapshot(None) is None


@pytest.mark.parametrize("kind", ["budget", "cash", "invoice", "act", "mpp", "copy"])
def test_each_manual_creator_inherits_contract_vat_without_changing_gross(vat_world, kind):
    w = vat_world
    if kind == "budget":
        result = api.create_budget(api.BudgetCreate(project_id=w.project.id, contract_id=w.contract.id,
            category="Материалы", description="Synthetic VAT budget", planned_amount="122.00"), w.db, w.user)
        row = w.db.get(BudgetLine, result["id"])
        amount = row.planned_amount
    elif kind in {"cash", "invoice"}:
        values = dict(project_id=w.project.id, contract_id=w.contract.id, direction="outflow",
            title="Synthetic VAT invoice", planned_date=date(2026, 10, 5), planned_amount="122.00")
        if kind == "invoice":
            result = api.create_invoice_proposal(api.InvoiceProposalCreate(**values,
                budget_line_id=w.budget.id, schedule_item_id=w.stage.id), w.db, w.user)
        else:
            result = api.create_cash_flow(api.CashFlowCreate(**values), w.db, w.user)
        row = w.db.get(CashFlowEntry, result["id"])
        amount = row.planned_amount
    elif kind == "act":
        result = api.create_act(api.ActCreate(project_id=w.project.id, contract_id=w.contract.id,
            budget_line_id=w.budget.id, number="VAT-ACT", title="Synthetic VAT act", amount="122.00"), w.db, w.user)
        row = w.db.get(AcceptanceAct, result["id"])
        amount = row.amount
    elif kind == "mpp":
        task = SimpleNamespace(is_summary=False, cost=Decimal("122.00"), external_uid="test-uid",
            planned_start=date(2026, 10, 4), planned_finish=date(2026, 10, 5), title="Synthetic MPP expense")
        result = api._create_mpp_cash_flow_proposals(w.db, payload=api.MppImportRequest(
            project_id=w.project.id, contract_id=w.contract.id, filename="synthetic.mpp",
            content_base64="", create_cash_flow_proposals=True), digest="0" * 64, tasks=[task],
            imported={task.external_uid: w.stage}, user=w.user)
        row = w.db.get(CashFlowEntry, result[0])
        amount = row.planned_amount
        assert row.direction == "outflow"
    else:
        source = cash(w)
        result = api.mutate_cash_flow_plan(source.id, api.CashFlowPlanMutationRequest(operation="copy",
            planned_date=date(2026, 10, 6), planned_amount="122.00", expected_record_version=source.record_version,
            idempotency_key="vat-copy-test"), w.db, w.user)
        row = w.db.get(CashFlowEntry, result["result_id"])
        amount = row.planned_amount
        assert source.vat_snapshot is None
    assert row.vat_snapshot == snapshot(w.contract)
    assert amount == Decimal("122.00")


@pytest.mark.parametrize("kind", ["budget", "cash_flow"])
def test_extracted_invoice_confirmation_inherits_chosen_contract(vat_world, kind):
    w = vat_world
    source, version, digest = document(w, "Итого 122 руб. Назначение: материалы.", "synthetic-invoice.txt")
    proposal = InvoiceExtractionProposal(project_id=w.project.id, source_document_id=source.id,
        source_document_version_id=version.id, source_document_sha256=digest, amount=Decimal("122.00"),
        selected_cost_category_id=w.category.id, payment_purpose="Synthetic materials",
        planned_date=date(2026, 10, 5), currency="RUB", extraction_method="regex", target_kind=kind)
    w.db.add(proposal); w.db.commit()
    result = api.confirm_invoice_extraction(proposal.id, api.InvoiceExtractionConfirm(
        contract_id=w.contract.id, budget_line_id=w.budget.id, schedule_item_id=w.stage.id), w.db, w.user)
    if kind == "budget":
        row = w.db.get(BudgetLine, result["created_budget_line_id"])
    else:
        row = w.db.get(CashFlowEntry, result["created_cash_flow_id"])
    assert row.vat_snapshot == snapshot(w.contract)
    assert row.planned_amount == Decimal("122.00")
    assert proposal.vat_snapshot == row.vat_snapshot
    assert result["vat_snapshot"] == row.vat_snapshot


@pytest.mark.parametrize("kind", ["budget", "cash-flow"])
def test_structured_import_inherits_contract_vat(vat_world, monkeypatch, kind):
    w = vat_world
    source, version, digest = document(w, "Synthetic tabular money")
    parsed = dict(rows=[dict(source_row=2, importable=True, source_coordinate="A2", title="Synthetic materials",
        category="Материалы", amount="122.00", direction="inflow", planned_date="2026-10-05",
        counterparty=None, excerpt="Synthetic evidence")])
    monkeypatch.setattr(api, "parse_structured_rows", lambda *_args, **_kwargs: parsed)
    result = api.structured_import(source.id, api.StructuredImportRequest(project_id=w.project.id,
        contract_id=w.contract.id, kind=kind, source_rows=[2], expected_document_version_id=version.id,
        expected_document_sha256=digest), w.db, w.user)
    row = w.db.get(BudgetLine if kind == "budget" else CashFlowEntry, result["created_ids"][0])
    assert row.vat_snapshot == snapshot(w.contract)
    assert row.planned_amount == Decimal("122.00")


def test_matrix_import_inherits_vat_for_budget_and_each_forecast(vat_world):
    from app.dds_article_budget import ArticleBudgetApplyRequest, ArticleBudgetPreviewRequest, apply_article_budget, preview_article_budget
    w = vat_world
    source, version, digest = document(w, "Статья\tГод\tянварь\tфевраль\tмарт\nМатериалы\t122\t61\t61\t0\n", "synthetic-matrix.xlsx")
    request = ArticleBudgetPreviewRequest(project_id=w.project.id, contract_id=w.contract.id,
        plan_year=2026, budget_revision=1, mode="import_forecast", category_by_article={2: w.category.id})
    proposal = preview_article_budget(source.id, request, w.db, w.user)
    result = apply_article_budget(source.id, ArticleBudgetApplyRequest(**request.model_dump(),
        preview_hash=proposal["preview_hash"], expected_document_version_id=version.id,
        expected_document_sha256=digest, idempotency_key="vat-matrix-test", owner_confirmed=True), w.db, w.user)
    rows = [w.db.get(BudgetLine, i) for i in result["created_budget_ids"]]
    forecasts = [w.db.get(CashFlowEntry, i) for i in result["created_cash_flow_ids"]]
    assert rows and forecasts
    assert all(row.vat_snapshot == snapshot(w.contract) for row in [*rows, *forecasts])
    assert rows[0].planned_amount == sum(row.planned_amount for row in forecasts) == Decimal("122.00")


@pytest.mark.parametrize("legacy", [False, True])
def test_payment_and_correction_preserve_invoice_tax_after_contract_changes(vat_world, legacy):
    w = vat_world
    row = cash(w, status="approved")
    row.vat_snapshot = None if legacy else snapshot(w.contract)
    original = row.vat_snapshot
    w.contract.vat_rate = Decimal("20.00")
    w.contract.record_version += 1
    w.db.commit()
    result = api.confirm_payment(row.id, api.PaymentConfirmation(actual_date=date(2026, 10, 5),
        idempotency_key="vat-payment-test"), w.db, w.user)
    event = w.db.get(PaymentEvent, result["payment_event_id"])
    assert event.vat_snapshot == row.vat_snapshot == original
    corrected = api.correct_payment(row.id, api.PaymentCorrection(actual_amount="121.00", actual_date=date(2026, 10, 6),
        supersedes_event_id=event.id, reason="Synthetic correction", idempotency_key="vat-correction-test"), w.db, w.user)
    assert w.db.get(PaymentEvent, corrected["payment_event_id"]).vat_snapshot == original
    assert row.planned_amount == Decimal("122.00")
    assert api.payment_events(row.id, w.db, w.user)[0]["vat_snapshot"] == original


@pytest.mark.parametrize("kind", ["budget", "cash-flow", "acts"])
def test_stale_proposed_tax_snapshot_blocks_approval_without_refresh(vat_world, kind):
    w = vat_world
    if kind == "budget":
        row = w.budget
        row.status = "proposed"
    elif kind == "cash-flow":
        row = cash(w)
    else:
        row = AcceptanceAct(project_id=w.project.id, contract_id=w.contract.id, number="VAT-stale",
            title="Synthetic stale act", amount=Decimal("122.00"), currency="RUB", status="proposed")
        w.db.add(row); w.db.flush()
    row.vat_snapshot = snapshot(w.contract)
    original = row.vat_snapshot
    w.contract.vat_rate = Decimal("20.00")
    w.contract.record_version += 1
    w.db.commit()
    with pytest.raises(HTTPException, match="VAT_SNAPSHOT_STALE") as exc:
        api.update_status(kind, row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    assert exc.value.status_code == 409
    assert row.status == "proposed" and row.vat_snapshot == original


def test_benign_contract_revision_does_not_invalidate_tax_provenance(vat_world):
    w = vat_world
    row = cash(w)
    row.vat_snapshot = snapshot(w.contract)
    original = dict(row.vat_snapshot)
    w.contract.title = "Renamed synthetic contract"
    w.contract.record_version += 1
    w.db.commit()
    result = api.update_status("cash-flow", row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    assert result["status"] == "approved"
    assert row.vat_snapshot == original


def test_legacy_snapshot_comparison_only_normalizes_missing_vat_field(vat_world):
    from app.dds_article_budget import _snapshot, _snapshot_matches
    w = vat_world
    row = cash(w)
    old = _snapshot(row)
    old.pop("vat_snapshot")
    assert _snapshot_matches(row, old)
    different = dict(old, title="Changed")
    assert not _snapshot_matches(row, different)
    different = dict(old)
    different.pop("currency")
    assert not _snapshot_matches(row, different)
    row.vat_snapshot = snapshot(w.contract)
    assert not _snapshot_matches(row, old)


def test_finance_serializers_include_snapshot_and_preserve_legacy_null(vat_world):
    w = vat_world
    result = api.create_cash_flow(api.CashFlowCreate(project_id=w.project.id, contract_id=w.contract.id,
        direction="inflow", title="Synthetic VAT display", planned_date=date(2026, 10, 5), planned_amount="122.00"), w.db, w.user)
    response = api.overview(w.project.id, w.db, w.user)
    created = next(r for r in response["cash_flow"] if r["id"] == result["id"])
    assert created["vat_snapshot"] == snapshot(w.contract)
    assert next(r for r in response["budget"] if r["id"] == w.budget.id)["vat_snapshot"] is None


def test_client_snapshot_cannot_override_server_contract_vat(vat_world):
    w = vat_world
    payload = api.CashFlowCreate(project_id=w.project.id, contract_id=w.contract.id, direction="inflow",
        title="Synthetic spoof attempt", planned_date=date(2026, 10, 5), planned_amount="122.00",
        vat_snapshot=dict(mode="none", rate=None))
    result = api.create_cash_flow(payload, w.db, w.user)
    assert w.db.get(CashFlowEntry, result["id"]).vat_snapshot == snapshot(w.contract)


def refresh_request(w, row, key="synthetic-vat-refresh"):
    return api.VatSnapshotRefresh(expected_state_hash=api._vat_refresh_state_hash(row),
        expected_contract_record_version=w.contract.record_version, idempotency_key=key)


@pytest.mark.parametrize("kind", ["budget", "cash-flow", "acts"])
def test_explicit_vat_refresh_changes_only_snapshot_and_version_and_replays(vat_world, kind):
    w = vat_world
    if kind == "budget":
        row = w.budget
        row.status = "proposed"
    elif kind == "cash-flow":
        row = cash(w)
    else:
        row = AcceptanceAct(project_id=w.project.id, contract_id=w.contract.id, budget_line_id=w.budget.id,
            number="VAT-refresh", title="Synthetic refresh act", amount=Decimal("122.00"),
            act_date=date(2026, 10, 5), currency="RUB", status="proposed")
        w.db.add(row); w.db.flush()
    row.vat_snapshot = snapshot(w.contract)
    w.contract.vat_rate = Decimal("20.00")
    w.contract.record_version += 1
    w.db.commit()
    request = refresh_request(w, row)
    from app.dds_article_budget import _snapshot
    before = _snapshot(row)
    first = api.refresh_vat_snapshot(kind, row.id, request, w.db, w.user)
    replay = api.refresh_vat_snapshot(kind, row.id, request, w.db, w.user)
    after = _snapshot(row)
    assert row.vat_snapshot == snapshot(w.contract, rate="20.00")
    assert first["changed"] is True and first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["vat_snapshot"] == first["vat_snapshot"]
    assert {k: v for k, v in before.items() if k not in {"vat_snapshot", "record_version"}} == {
        k: v for k, v in after.items() if k not in {"vat_snapshot", "record_version"}}
    if kind != "acts":
        assert after["record_version"] == before["record_version"] + 1
    audits = list(w.db.scalars(select(AuditLog).where(AuditLog.action == "finance_vat_refreshed")))
    assert len(audits) == 1
    receipt = json.loads(audits[0].details)
    assert receipt["before_vat_snapshot"] == before["vat_snapshot"]
    assert receipt["after_vat_snapshot"] == row.vat_snapshot
    assert receipt["user"] == w.user.id


def test_explicit_refresh_can_capture_legacy_null_without_mass_backfill(vat_world):
    w = vat_world
    row = cash(w)
    other = cash(w)
    result = api.refresh_vat_snapshot("cash-flow", row.id, refresh_request(w, row), w.db, w.user)
    assert result["changed"] is True and row.vat_snapshot == snapshot(w.contract)
    assert other.vat_snapshot is None and w.budget.vat_snapshot is None


@pytest.mark.parametrize("changed_field", ["state", "contract"])
def test_refresh_rejects_stale_state_and_contract_without_audit(vat_world, changed_field):
    w = vat_world
    row = cash(w)
    request = refresh_request(w, row)
    if changed_field == "state":
        row.planned_date = date(2026, 10, 6)
    else:
        w.contract.record_version += 1
    w.db.commit()
    with pytest.raises(HTTPException) as exc:
        api.refresh_vat_snapshot("cash-flow", row.id, request, w.db, w.user)
    assert exc.value.status_code == 409
    assert row.vat_snapshot is None
    assert w.db.query(AuditLog).filter_by(action="finance_vat_refreshed").count() == 0


@pytest.mark.parametrize("status", ["approved", "paid", "received", "cancelled"])
def test_refresh_cannot_modify_finalized_or_cancelled_rows(vat_world, status):
    w = vat_world
    row = cash(w, status=status)
    with pytest.raises(HTTPException) as exc:
        api.refresh_vat_snapshot("cash-flow", row.id, refresh_request(w, row), w.db, w.user)
    assert exc.value.status_code == 409 and row.vat_snapshot is None


def test_refresh_rejects_replaced_source_pin(vat_world):
    w = vat_world
    source, version, digest = document(w, "Synthetic invoice first version")
    row = cash(w, source_document_id=source.id, source_document_version_id=version.id, source_document_sha256=digest)
    request = refresh_request(w, row)
    source.current_version = 2
    w.db.add(DocumentVersion(document_id=source.id, version_number=2, content="Synthetic invoice revised"))
    w.db.commit()
    with pytest.raises(HTTPException) as exc:
        api.refresh_vat_snapshot("cash-flow", row.id, request, w.db, w.user)
    assert exc.value.status_code == 409 and row.vat_snapshot is None


def test_refresh_audit_and_change_rollback_together(vat_world, monkeypatch):
    w = vat_world
    row = cash(w)
    original_hash = api._vat_refresh_state_hash(row)
    request = refresh_request(w, row)
    def fail_commit():
        raise RuntimeError("synthetic commit failure")
    monkeypatch.setattr(w.db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        api.refresh_vat_snapshot("cash-flow", row.id, request, w.db, w.user)
    w.db.expire_all()
    assert row.vat_snapshot is None and api._vat_refresh_state_hash(row) == original_hash
    assert w.db.query(AuditLog).filter_by(action="finance_vat_refreshed").count() == 0


def test_refresh_rejects_idempotency_key_with_different_command(vat_world):
    w = vat_world
    row = cash(w)
    api.refresh_vat_snapshot("cash-flow", row.id, refresh_request(w, row), w.db, w.user)
    request = refresh_request(w, row)
    with pytest.raises(HTTPException, match="IDEMPOTENCY_CONFLICT"):
        api.refresh_vat_snapshot("cash-flow", row.id, request, w.db, w.user)
    assert w.db.query(AuditLog).filter_by(action="finance_vat_refreshed").count() == 1


def test_pending_refresh_metadata_is_server_calculated_and_finalized_unavailable(vat_world):
    w = vat_world
    row = cash(w)
    row.vat_snapshot = snapshot(w.contract)
    w.contract.vat_rate = Decimal("20.00")
    w.contract.record_version += 1
    w.db.commit()
    overview = api.overview(w.project.id, w.db, w.user)
    pending = next(r for r in overview["cash_flow"] if r["id"] == row.id)
    assert pending["vat_snapshot_stale"] is True
    assert pending["vat_refresh_state_hash"] == api._vat_refresh_state_hash(row)
    assert pending["vat_contract_record_version"] == w.contract.record_version
    assert pending["vat_proposed_snapshot"] == snapshot(w.contract, rate="20.00")
    finalized = next(r for r in overview["budget"] if r["id"] == w.budget.id)
    assert finalized["vat_refresh_state_hash"] is None
    assert finalized["vat_proposed_snapshot"] is None


def test_explicit_contract_change_on_proposal_audits_tax_before_and_after(vat_world):
    w = vat_world
    row = cash(w, direction="outflow", vat_snapshot=snapshot(w.contract))
    original = dict(row.vat_snapshot)
    contract = Contract(project_id=w.project.id, number="VAT-OTHER", title="Synthetic new contract",
        vat_mode="none", vat_rate=None)
    w.db.add(contract); w.db.flush()
    baseline = ScheduleBaseline(project_id=w.project.id, contract_id=contract.id, name="Other schedule",
        created_by_user_id=w.user.id, version=2)
    budget = BudgetLine(project_id=w.project.id, contract_id=contract.id, category="Материалы",
        description="Other budget", planned_amount=Decimal("122.00"), currency="RUB")
    w.db.add_all([baseline, budget]); w.db.flush()
    stage = ScheduleItem(project_id=w.project.id, baseline_id=baseline.id, title="Other stage")
    w.db.add(stage); w.db.commit()
    api.link_cash_flow_controls(row.id, api.CashFlowControlLinks(contract_id=contract.id,
        budget_line_id=budget.id, schedule_item_id=stage.id), w.db, w.user)
    assert row.vat_snapshot == snapshot(contract, "none", None)
    audit = w.db.scalar(select(AuditLog).where(AuditLog.action == "cash_flow_controls_linked"))
    assert '"vat_snapshot"' in audit.details
    assert json.dumps(original, ensure_ascii=False) in audit.details


def test_linking_controls_with_same_contract_preserves_legacy_tax_null(vat_world):
    w = vat_world
    row = cash(w, direction="outflow")
    api.link_cash_flow_controls(row.id, api.CashFlowControlLinks(contract_id=w.contract.id,
        budget_line_id=w.budget.id, schedule_item_id=w.stage.id), w.db, w.user)
    assert row.vat_snapshot is None


def test_payment_reversal_keeps_original_tax_snapshot(vat_world):
    w = vat_world
    row = cash(w, status="approved", vat_snapshot=snapshot(w.contract))
    paid = api.confirm_payment(row.id, api.PaymentConfirmation(actual_date=date(2026, 10, 5)), w.db, w.user)
    w.contract.vat_rate = Decimal("20.00")
    w.contract.record_version += 1
    w.db.commit()
    reversed_result = api.reverse_payment(row.id, api.PaymentReversal(supersedes_event_id=paid["payment_event_id"],
        reason="Synthetic reversal", idempotency_key="vat-reversal-test"), w.db, w.user)
    assert w.db.get(PaymentEvent, reversed_result["payment_event_id"]).vat_snapshot == row.vat_snapshot


def test_refresh_requires_project_manager(vat_world, user_factory):
    w = vat_world
    row = cash(w)
    outsider = user_factory(is_admin=False)
    with pytest.raises(HTTPException) as exc:
        api.refresh_vat_snapshot("cash-flow", row.id, refresh_request(w, row), w.db, outsider)
    assert exc.value.status_code == 403
    assert row.vat_snapshot is None
    assert w.db.query(AuditLog).filter_by(action="finance_vat_refreshed").count() == 0
