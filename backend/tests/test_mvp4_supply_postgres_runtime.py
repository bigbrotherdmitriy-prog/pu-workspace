"""Real SupplyService concurrency on an explicitly owned, migrated PostgreSQL DB.

The orchestrator owns database creation/migration/removal. No create_all, schema
reset, provider requests, orders, signatures or payments run in the PG fixture.
Local SQLite checks below validate fixture/service behavior, not PG concurrency.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import re
from threading import Barrier
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import Base
from app.models.audit_log import AuditLog
from app.models.execution_finance import CashFlowEntry, CashFlowFactHistory, ScheduleBaseline
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_pilot import (
    ConnectionIdentity, Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion,
)
from app.mvp4.supply.contracts import (
    CreateDdsProposal, CreateSupplyRequest, EvidenceLink, PrepareOrder,
    ProposeAcceptanceAct, RecordDelivery, RecordOrder, VersionedCommand,
)
from app.mvp4.supply.models import SupplyCase, SupplyCaseVersion, SupplyCommandReceipt
from app.mvp4.supply.service import SupplyConflict, SupplyDenied, SupplyService
from app.schema import CURRENT_SCHEMA_REVISION
from test_mvp4_budget_dds import _chain


def _safe_url(value):
    try:
        url = make_url(value)
    except Exception:
        raise ValueError("owned_mvp4_postgres_url_required") from None
    hosts = {"localhost", "127.0.0.1", "::1", "db"}
    if os.getenv("GITHUB_ACTIONS") == "true":
        hosts.add("postgres")
    if (url.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
        or url.host not in hosts or url.query
        or re.fullmatch(r"puw_mvp4_test_[a-z0-9_]+", url.database or "") is None):
        raise ValueError("owned_mvp4_postgres_url_required")
    return url


@pytest.fixture
def pg_supply():
    value = os.getenv("PUW_MVP4_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: isolated MVP4 PostgreSQL is not configured")
    engine = create_engine(_safe_url(value), hide_parameters=True, connect_args={
        "connect_timeout": 5, "options": "-clock_timeout=8000 -cstatement_timeout=15000",
    })
    try:
        with engine.connect() as db:
            assert db.scalar(text("SELECT current_database()")) == engine.url.database
            assert list(db.scalars(text("SELECT version_num FROM alembic_version"))) == [CURRENT_SCHEMA_REVISION]
            assert CURRENT_SCHEMA_REVISION == "a54f001c0a18"
            assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
        yield engine
    finally:
        engine.dispose()


def _seed(engine, operation):
    """Unique rows and exact evidence links; every supply transition is real service code."""
    token = uuid4().hex
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        manager = User(name="Synthetic supply manager", email=f"supply-manager-{token}@example.test", is_admin=False)
        editor = User(name="Synthetic supply editor", email=f"supply-editor-{token}@example.test", is_admin=False)
        db.add_all([manager, editor]); db.flush()
        project, contract, stage, task, budget, _document, document_version = _chain(db, manager)
        baseline = db.get(ScheduleBaseline, stage.baseline_id)
        db.add_all([ProjectMember(project_id=project.id, user_id=manager.id, role="manager"),
                    ProjectMember(project_id=project.id, user_id=editor.id, role="editor")])
        identity = ConnectionIdentity(id=str(uuid4()), organization_id=project.organization_id,
            provider="synthetic", account_key=token, state="verified", credential_generation=1, verified_at=now)
        db.add(identity); db.flush()
        source = SourceReference(id=str(uuid4()), organization_id=project.organization_id,
            origin_project_id=project.id, identity_id=identity.id, namespace="synthetic-supply",
            external_id=token, external_id_kind="stable_id", object_kind="file",
            canonical_locator={"kind": "opaque_id", "value": token})
        db.add(source); db.flush()
        version = SourceVersion(id=str(uuid4()), organization_id=project.organization_id, source_id=source.id,
            observation_key=token, provider_revision="synthetic-v1", consistency="revision_bound",
            locator_at_observation={"kind": "opaque_id", "value": token}, integrity=[],
            observed_at=now, legacy_document_version_id=document_version.id)
        db.add(version); db.flush()
        db.add(SourceCurrent(organization_id=project.organization_id, source_id=source.id, version_id=version.id))
        evidence = Evidence(id=str(uuid4()), organization_id=project.organization_id, source_id=source.id,
            source_version_id=version.id, locator={"kind": "text_range", "start": 1, "end": 8},
            extractor={"name": "synthetic", "version": "1"}, confidence=.96,
            confidence_kind="model", extracted_at=now)
        db.add(evidence); db.flush()
        db.add(EvidenceAssessment(evidence_id=evidence.id, organization_id=project.organization_id,
            verification="verified", freshness="fresh", availability="available", checked_at=now,
            valid_until=now + timedelta(hours=1), reviewed_by=manager.id, reviewed_at=now))
        db.flush()
        link = EvidenceLink(evidence_id=UUID(evidence.id), source_version_id=UUID(version.id),
                            document_version_id=document_version.id)
        request = CreateSupplyRequest(command_key=f"request:{token}", organization_id=project.organization_id,
            project_id=project.id, contract_id=contract.id, schedule_baseline_id=baseline.id,
            schedule_baseline_version=baseline.version, schedule_item_id=stage.id, task_id=task.id,
            evidence=link, title="Synthetic materials", supplier="Synthetic supplier",
            requested_quantity=Decimal("10"), unit="pcs", unit_price=Decimal("1250"), currency="RUB")
        fixture = SimpleNamespace(project=project.id, organization=project.organization_id,
            manager=manager.id, editor=editor.id, contract=contract.id, stage=stage.id,
            budget=budget.id, evidence=link, request=request, case_id=None, version=0)
        service = SupplyService()
        if operation != "request":
            result = service.create_request(db, actor_user_id=editor.id, command=request)
            fixture.case_id = result.supply_case_id
            common = dict(organization_id=fixture.organization, project_id=fixture.project,
                          supply_case_id=fixture.case_id)
            steps = [
                (service.approve_request, manager.id, VersionedCommand(command_key="seed:approve:request", expected_version=1)),
                (service.prepare_order, editor.id, PrepareOrder(command_key="seed:prepare:order", expected_version=2,
                    ordered_quantity=Decimal("10"), order_reference="SYN-PO")),
                (service.approve_order, manager.id, VersionedCommand(command_key="seed:approve:order", expected_version=3)),
                (service.record_order, editor.id, RecordOrder(command_key="seed:record:order", expected_version=4, evidence=link)),
                (service.record_delivery, editor.id, RecordDelivery(command_key="seed:record:delivery", expected_version=5,
                    delivered_quantity=Decimal("10"), evidence=link)),
                (service.propose_acceptance_act, editor.id, ProposeAcceptanceAct(command_key="seed:propose:act",
                    expected_version=6, accepted_quantity=Decimal("10"), act_number="SYN-ACT", evidence=link)),
            ]
            count = {"request_approval": 0, "order": 1, "order_approval": 2, "delivery": 4, "dds": 4, "act": 6}[operation]
            for method, actor, command in steps[:count]:
                result = method(db, **common, actor_user_id=actor, command=command)
            fixture.version = result.record_version
        db.commit()
        return fixture


def _command(fixture, operation, variant=0):
    common = dict(command_key=f"race:{operation}:{variant:04}", expected_version=fixture.version)
    if operation == "request":
        return fixture.request
    if operation == "order":
        return PrepareOrder(**common, ordered_quantity=Decimal(4 + variant * 2), order_reference=f"SYN-PO-{variant}")
    if operation == "delivery":
        return RecordDelivery(**common, delivered_quantity=Decimal(4 + variant * 2), evidence=fixture.evidence)
    if operation == "dds":
        return CreateDdsProposal(**common, contract_id=fixture.contract, schedule_item_id=fixture.stage,
            budget_line_id=fixture.budget, planned_date=date(2026, 10, 1), amount=Decimal(1000 + variant * 1000),
            currency="RUB", evidence_assessment_version=1, evidence=fixture.evidence)
    return VersionedCommand(**common)


def _invoke(db, fixture, operation, command):
    service = SupplyService()
    if operation == "request":
        return service.create_request(db, actor_user_id=fixture.editor, command=command)
    method = {"request_approval": service.approve_request, "order": service.prepare_order,
              "order_approval": service.approve_order, "delivery": service.record_delivery,
              "act": service.approve_acceptance_act, "dds": service.create_dds_proposal}[operation]
    return method(db, organization_id=fixture.organization, project_id=fixture.project,
        supply_case_id=fixture.case_id, actor_user_id=(fixture.manager if operation in
            {"request_approval", "order_approval", "act"} else fixture.editor), command=command)


def _race(engine, fixture, operation, commands):
    """Observe both real sessions waiting on PG locks before releasing the winner."""
    barrier = Barrier(3)
    application = f"puw_supply_race_{uuid4().hex}"
    def invoke(command):
        try:
            with Session(engine) as db:
                db.execute(text("SELECT set_config('application_name', :name, true)"), {"name": application})
                # Deliberately retain an earlier identity-map read across lock wait.
                # FOR UPDATE must refresh the CAS version after another commit.
                prior = db.get(SupplyCase, fixture.case_id) if fixture.case_id else None
                barrier.wait(timeout=8)
                try:
                    result = _invoke(db, fixture, operation, command)
                    db.commit()
                    return {"result": result.model_dump(), "preloaded": prior is not None}
                except (SupplyConflict, SupplyDenied) as exc:
                    db.rollback()
                    return {"conflict": str(exc)}
        except Exception as exc:
            # Never return SQL, parameters, DSNs or driver exception messages.
            return {"failure_type": type(exc).__name__}

    with Session(engine) as blocker, ThreadPoolExecutor(max_workers=2) as executor:
        model, identifier = (SupplyCase, fixture.case_id) if fixture.case_id else (Project, fixture.project)
        blocker.scalar(select(model).where(model.id == identifier).with_for_update())
        futures = [executor.submit(invoke, command) for command in commands]
        waiting = 0
        try:
            barrier.wait(timeout=8)
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    waiting = observer.scalar(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name = :name AND wait_event_type = 'Lock'"), {"name": application})
                    if waiting == 2:
                        break
                    sleep(.01)
        finally:
            blocker.rollback()
        results = [future.result(timeout=20) for future in futures]
    assert waiting == 2, "both PostgreSQL transactions must demonstrably contend before release"
    assert all("failure_type" not in result for result in results), results
    return results


def _state(engine, fixture):
    with Session(engine) as db:
        row = db.scalar(select(SupplyCase).where(SupplyCase.project_id == fixture.project))
        case_id = row.id if row else -1
        history = list(db.scalars(select(SupplyCaseVersion).where(
            SupplyCaseVersion.supply_case_id == case_id).order_by(SupplyCaseVersion.sequence)))
        return dict(case_id=case_id, version=row.record_version if row else 0,
            history=[(h.id, h.sequence, h.event, h.snapshot) for h in history],
            receipts=db.scalar(select(func.count()).select_from(SupplyCommandReceipt).where(
                SupplyCommandReceipt.supply_case_id == case_id)),
            audits=db.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.entity_type == "supply_case", AuditLog.entity_id == case_id)),
            dds=list(db.scalars(select(CashFlowEntry.id).where(CashFlowEntry.project_id == fixture.project))),
            delivered=row.delivered_quantity if row else Decimal(0),
            accepted=row.accepted_quantity if row else Decimal(0),
            ordered=row.ordered_quantity if row else Decimal(0),
            external=row.external_action_status if row else "not_created")


def _assert_one_effect(engine, fixture, operation, before):
    after = _state(engine, fixture)
    assert after["version"] == before["version"] + 1
    assert after["history"][:-1] == before["history"]
    assert after["receipts"] == before["receipts"] + 1
    assert after["audits"] == before["audits"] + 1
    assert after["external"] == "not_created"
    assert len(after["dds"]) == len(before["dds"]) + (operation == "dds")
    if operation == "act":
        assert after["accepted"] == after["delivered"] == after["ordered"] == Decimal("10")
    if operation == "delivery":
        assert after["delivered"] in {Decimal("4"), Decimal("6")}
    if operation == "order":
        assert after["ordered"] in {Decimal("4"), Decimal("6")}
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(SupplyCase).where(SupplyCase.project_id == fixture.project)) == 1
        cash = list(db.scalars(select(CashFlowEntry).where(CashFlowEntry.project_id == fixture.project)))
        assert all(item.status == "proposed" and item.actual_amount == 0 and item.actual_date is None
                   and item.review_status == "pending_confirmation" for item in cash)
        assert db.scalar(select(func.count()).select_from(CashFlowFactHistory).where(
            CashFlowFactHistory.cash_flow_entry_id.in_(after["dds"]))) == 0
    return after


@pytest.mark.parametrize("operation", ["request", "request_approval", "order_approval", "act", "dds"])
def test_postgres_duplicate_supply_commands_create_one_effect(pg_supply, operation):
    fixture = _seed(pg_supply, operation)
    before = _state(pg_supply, fixture)
    command = _command(fixture, operation)
    results = _race(pg_supply, fixture, operation, [command, command])
    assert all("result" in result for result in results), results
    assert sorted(result["result"]["already_applied"] for result in results) == [False, True]
    first, second = (result["result"] for result in results)
    assert {k: v for k, v in first.items() if k != "already_applied"} == {
        k: v for k, v in second.items() if k != "already_applied"}
    _assert_one_effect(pg_supply, fixture, operation, before)


@pytest.mark.parametrize("operation", ["order", "delivery", "act", "dds"])
def test_postgres_stale_supply_updates_preserve_one_effect(pg_supply, operation):
    fixture = _seed(pg_supply, operation)
    before = _state(pg_supply, fixture)
    commands = [_command(fixture, operation, variant) for variant in (0, 1)]
    results = _race(pg_supply, fixture, operation, commands)
    assert sum("result" in result for result in results) == 1, results
    assert [result["conflict"] for result in results if "conflict" in result] == ["record_version_conflict"]
    _assert_one_effect(pg_supply, fixture, operation, before)


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://synthetic@remote.example.test/puw_mvp4_test_one",
    "postgresql+psycopg://synthetic@localhost/production",
    "postgresql+psycopg://synthetic@localhost/puw_mvp4_test_one?host=remote.example.test",
    "postgresql+psycopg://synthetic@localhost/puw_mvp4_test_",
    "sqlite:///puw_mvp4_test_one",
])
def test_supply_postgres_guard_refuses_nonisolated_targets(url):
    with pytest.raises(ValueError, match="owned_mvp4_postgres_url_required"):
        _safe_url(url)


@pytest.fixture
def local_supply(tmp_path):
    """File-backed SQLite only, so the local regression also has distinct connections."""
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic-supply.sqlite'}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation", ["request", "request_approval", "order", "order_approval", "delivery", "act", "dds"])
def test_local_supply_fixture_uses_real_authority_and_evidence(local_supply, operation):
    engine = local_supply
    fixture = _seed(engine, operation)
    with Session(engine) as db:
        assert db.scalar(select(ProjectMember.role).where(ProjectMember.project_id == fixture.project,
            ProjectMember.user_id == fixture.manager)) == "manager"
        if operation == "act":
            with pytest.raises(SupplyDenied, match="resource_unavailable"):
                SupplyService().approve_acceptance_act(db, organization_id=fixture.organization,
                    project_id=fixture.project, supply_case_id=fixture.case_id, actor_user_id=fixture.editor,
                    command=_command(fixture, "act"))
        db.rollback()
        before = _state(engine, fixture)
        _invoke(db, fixture, operation, _command(fixture, operation))
        db.commit()
    _assert_one_effect(engine, fixture, operation, before)


@pytest.mark.parametrize("operation", ["order", "delivery", "act", "dds"])
def test_local_preloaded_session_rechecks_supply_cas_after_other_commit(local_supply, operation):
    """Sequential SQLite regression for stale ORM reads, not PG concurrency evidence."""
    engine = local_supply
    fixture = _seed(engine, operation)
    before = _state(engine, fixture)
    with Session(engine) as stale, Session(engine) as winner:
        prior = stale.get(SupplyCase, fixture.case_id)
        assert prior.record_version == fixture.version
        _invoke(winner, fixture, operation, _command(fixture, operation, 0))
        winner.commit()
        with pytest.raises(SupplyConflict, match="record_version_conflict"):
            _invoke(stale, fixture, operation, _command(fixture, operation, 1))
        stale.rollback()
    _assert_one_effect(engine, fixture, operation, before)
