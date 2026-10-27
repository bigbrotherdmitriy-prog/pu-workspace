from datetime import date
from decimal import Decimal

from app.models.contract_stage import ContractStage
from app.models.execution_finance import AcceptanceAct, CashFlowEntry
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.work_center_hud import hud_extra, stages_by_object


def _world(db_session):
    org = Organization(name="HUD org")
    db_session.add(org); db_session.flush()
    project = Project(name="HUD project", organization_id=org.id)
    db_session.add(project); db_session.flush()
    contract = Contract(project_id=project.id, number="C-1", title="Заказчик", counterparty="ООО «БУЛАТ»",
                        status="active", contract_kind="customer")
    db_session.add(contract); db_session.flush()
    return project, contract


def test_stage_states_cover_signed_draft_paid_late_and_ahead(db_session):
    project, contract = _world(db_session)
    rows = []
    for number, finish in ((1, date(2026, 1, 31)), (2, date(2026, 2, 28)), (3, date(2026, 3, 31)),
                           (4, date(2026, 4, 30)), (5, date(2099, 1, 31))):
        stage = ContractStage(project_id=project.id, contract_id=contract.id, object_name="ГОРОДЕЦ",
                              stage_number=number, title=f"Этап {number}", amount=Decimal("100.00"), planned_finish=finish)
        db_session.add(stage); rows.append(stage)
    db_session.flush()
    db_session.add(AcceptanceAct(project_id=project.id, contract_id=contract.id, stage_id=rows[0].id,
                                 number="1", title="a", amount=Decimal("100"), status="signed"))
    db_session.add(AcceptanceAct(project_id=project.id, contract_id=contract.id, stage_id=rows[1].id,
                                 number="2", title="b", amount=Decimal("100"), status="draft"))
    # 100 received on this object covers the first stage in order; stage 3 has nothing and is over, 5 is ahead
    db_session.add(CashFlowEntry(project_id=project.id, contract_id=contract.id, direction="inflow",
                                 title="Этапы Городец", planned_date=date(2026, 1, 31), planned_amount=Decimal("0"),
                                 actual_amount=Decimal("100"), actual_date=date(2026, 2, 1), status="received"))
    db_session.flush()
    groups = stages_by_object(db_session, project.id, date(2026, 10, 27))
    assert len(groups) == 1 and groups[0]["name"] == "ГОРОДЕЦ"
    assert [s["state"] for s in groups[0]["stages"]] == ["ok", "draft", "late", "late", "ahead"]
    assert groups[0]["price"] == "500.00" and groups[0]["acted"] == "100.00"
    extra = hud_extra(db_session, project.id, date(2026, 10, 27))
    assert extra["invoices_to_check"] == 0 and extra["objects"][0]["name"] == "ГОРОДЕЦ"


def test_id_readiness_reports_the_number_of_missing_documents(db_session, monkeypatch):
    import app.executive_docs as executive_docs
    from app.models.executive_docs import IdSource

    project, _contract = _world(db_session)
    db_session.add(IdSource(project_id=project.id, object_name="ДУБНА", provider="local", folder_id="x", folder_name="ИД"))
    db_session.flush()
    monkeypatch.setattr(executive_docs, "object_report", lambda *_a, **_k: {"percent": 38, "required_total": 8, "missing": ["a", "b", "c", "d", "e"]})
    extra = hud_extra(db_session, project.id, date(2026, 10, 27))
    assert extra["id_readiness"] == [{"object": "ДУБНА", "percent": 38, "required_total": 8, "missing": 5}]
