from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

from app.api import project_search as search_api
from app.models.ai_secretary import Message
from app.models.contract_document_link import ContractDocumentLink
from app.models.document import Document
from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.mvp3 import project_search as service
from app.mvp3.project_search import SearchDenied, SearchFilters, SearchValidationError


@pytest.fixture(autouse=True)
def _cursor_secret(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "project-search-test-secret-32-bytes-minimum")


def _project(db, organization, user, *, name="Северный участок", role="viewer"):
    row = Project(name=name, organization_id=organization.id)
    db.add(row)
    db.flush()
    db.add(ProjectMember(project_id=row.id, user_id=user.id, role=role))
    db.flush()
    return row


def _task(db, project, user, *, title, source_id):
    row = Task(
        project_id=project.id,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        title=title,
        status="assigned",
        priority="normal",
        due_date=date(2026, 9, 20),
        source_type="manual",
        source_file_id=source_id,
        source_file_name="private-source.pdf",
        source_excerpt="must never be returned",
        source_excerpt_hash=(source_id * 64)[:64],
        confidence=1.0,
        needs_review=False,
    )
    db.add(row)
    db.flush()
    return row


def _world(db, user_factory):
    owner = user_factory(name="Search owner")
    second_member = user_factory(name="Second member")
    outsider = user_factory(name="Outsider")
    admin = user_factory(name="Admin", is_admin=True)
    organization = Organization(name="Search tenant")
    foreign_organization = Organization(name="Foreign tenant")
    db.add_all([organization, foreign_organization])
    db.flush()
    project = _project(db, organization, owner, role="manager")
    db.add(ProjectMember(project_id=project.id, user_id=second_member.id, role="viewer"))
    other_project = _project(db, organization, owner, name="Другой проект")
    foreign = _project(db, foreign_organization, outsider, name="SECRET project")
    contract = Contract(
        project_id=project.id,
        number="Д-42",
        title="Монтаж фасада",
        counterparty="ООО Синтетика",
        signed_at=date(2026, 8, 15),
    )
    foreign_contract = Contract(
        project_id=foreign.id,
        number="SECRET-1",
        title="Hidden contract",
        counterparty="Hidden counterparty",
    )
    db.add_all([contract, foreign_contract])
    db.flush()
    document = Document(
        project_id=project.id,
        name="Договор Д-42.pdf",
        source="local_upload",
        status="ready",
        notes="private notes",
        summary="private summary",
        external_id="provider-secret",
        source_modified_at=datetime(2026, 9, 3, 10, tzinfo=timezone.utc),
    )
    foreign_document = Document(
        project_id=foreign.id,
        name="SECRET payroll.pdf",
        source="local_upload",
        status="ready",
    )
    db.add_all([document, foreign_document])
    db.flush()
    db.add(ContractDocumentLink(
        project_id=project.id,
        contract_id=contract.id,
        document_id=document.id,
    ))
    task = _task(db, project, owner, title="Проверить фасад", source_id="task-a")
    _task(db, foreign, outsider, title="SECRET internal", source_id="task-b")
    obligation = Obligation(
        project_id=project.id,
        contract_id=contract.id,
        owner_user_id=owner.id,
        task_id=task.id,
        title="Передать акт",
        status="confirmed",
        due_date=date(2026, 9, 21),
        source_type="message",
        source_id="source-obligation",
        source_name="secret-source.eml",
        source_excerpt="private obligation evidence",
        source_hash="1" * 64,
        confidence=0.95,
    )
    db.add(obligation)
    db.flush()
    risk = Risk(
        project_id=project.id,
        owner_user_id=owner.id,
        obligation_id=obligation.id,
        task_id=task.id,
        title="Риск поставки",
        description="private risk body",
        criticality="high",
        status="confirmed",
        source_type="message",
        source_id="source-risk",
        source_name="secret-risk.eml",
        source_excerpt="private risk evidence",
        source_hash="2" * 64,
        confidence=0.9,
    )
    decision = Decision(
        project_id=project.id,
        initiator_user_id=owner.id,
        obligation_id=obligation.id,
        task_id=task.id,
        question="Согласовать замену материала",
        options="private options",
        status="needs_confirmation",
        source_type="message",
        source_id="source-decision",
        source_name="secret-decision.eml",
        source_excerpt="private decision evidence",
        source_hash="3" * 64,
        confidence=0.9,
    )
    db.add_all([risk, decision])
    db.flush()
    confirmed_message = Message(
        organization_id=organization.id,
        project_id=project.id,
        contract_id=contract.id,
        created_by_user_id=owner.id,
        source_type="email",
        source_external_id="mail-confirmed",
        source_name="Акт по фасаду",
        source_url="https://provider.example/private",
        source_sender="private@example.test",
        content="private body",
        attachments_json='[{"id":"private"}]',
        summary="private summary",
        context_confidence=1.0,
        context_evidence="private evidence",
        context_confirmed=True,
        status="processed",
    )
    unconfirmed_message = Message(
        organization_id=organization.id,
        project_id=project.id,
        created_by_user_id=owner.id,
        source_type="email",
        source_external_id="mail-unconfirmed",
        source_name="SECRET unconfirmed candidate",
        content="private body",
        attachments_json="[]",
        summary="private summary",
        context_confidence=0.2,
        context_evidence="private evidence",
        context_confirmed=False,
        status="needs_review",
    )
    db.add_all([confirmed_message, unconfirmed_message])
    db.commit()
    return {
        "owner": owner,
        "second_member": second_member,
        "outsider": outsider,
        "admin": admin,
        "organization": organization,
        "project": project,
        "other_project": other_project,
        "foreign": foreign,
        "contract": contract,
        "document": document,
        "task": task,
        "obligation": obligation,
        "risk": risk,
        "decision": decision,
        "message": confirmed_message,
    }


def test_search_covers_all_types_with_minimal_redacted_projection(db_session, user_factory):
    world = _world(db_session, user_factory)
    result = service.project_search(
        db_session,
        project_id=world["project"].id,
        actor_user_id=world["owner"].id,
        filters=SearchFilters(),
        limit=100,
    )

    assert {item["entity_type"] for item in result["items"]} == service.ALLOWED_TYPES
    assert all(item["project_id"] == world["project"].id for item in result["items"])
    assert "SECRET" not in str(result)
    allowed = {
        "entity_type", "entity_id", "name", "date", "project_id",
        "contract_id", "counterparty", "status", "navigation",
    }
    assert all(set(item) == allowed for item in result["items"])
    serialized = str(result).casefold()
    for forbidden in (
        "private body", "private summary", "private evidence", "private notes",
        "provider-secret", "private@example.test", "provider.example", "attachments",
    ):
        assert forbidden not in serialized
    assert result["external_actions_created"] is False


def test_search_requires_explicit_membership_even_for_admin_and_is_exact_project(db_session, user_factory):
    world = _world(db_session, user_factory)
    for actor in (world["outsider"], world["admin"]):
        with pytest.raises(SearchDenied, match="scope_unavailable"):
            service.project_search(
                db_session,
                project_id=world["project"].id,
                actor_user_id=actor.id,
                filters=SearchFilters(),
            )

    with pytest.raises(HTTPException) as error:
        search_api.search_project(
            project_id=world["project"].id,
            q=None,
            types=None,
            date_from=None,
            date_to=None,
            contract_id=None,
            counterparty=None,
            cursor=None,
            limit=50,
            db=db_session,
            user=world["admin"],
        )
    assert error.value.status_code == 404


def test_literal_wildcards_are_escaped_and_types_are_allowlisted(db_session, user_factory):
    world = _world(db_session, user_factory)
    db_session.add_all([
        Document(
            project_id=world["project"].id,
            name=r"literal 100%_done\file.pdf",
            source="local_upload",
            status="ready",
        ),
        Document(
            project_id=world["project"].id,
            name="literal 100XXdone-other.pdf",
            source="local_upload",
            status="ready",
        ),
    ])
    db_session.commit()

    result = service.project_search(
        db_session,
        project_id=world["project"].id,
        actor_user_id=world["owner"].id,
        filters=SearchFilters(query="%_done\\", types=("document",)),
    )
    assert [item["name"] for item in result["items"]] == [r"literal 100%_done\file.pdf"]
    attack = service.project_search(
        db_session,
        project_id=world["project"].id,
        actor_user_id=world["owner"].id,
        filters=SearchFilters(query="%' OR 1=1 --"),
    )
    assert attack["items"] == []
    with pytest.raises(SearchValidationError, match="invalid_type"):
        SearchFilters(types=("document", "raw_sql"))


def test_filters_apply_server_side_to_contract_counterparty_and_dates(db_session, user_factory):
    world = _world(db_session, user_factory)
    result = service.project_search(
        db_session,
        project_id=world["project"].id,
        actor_user_id=world["owner"].id,
        filters=SearchFilters(
            types=("contract", "document", "task", "obligation", "risk", "decision", "message"),
            contract_id=world["contract"].id,
            counterparty="синтетика",
            date_from=date(2026, 8, 1),
            date_to=date(2026, 9, 30),
        ),
        limit=100,
    )
    assert {item["entity_type"] for item in result["items"]} == {
        "contract", "document", "task", "obligation", "risk", "decision", "message",
    }
    assert all(item["contract_id"] == world["contract"].id for item in result["items"])

    with pytest.raises(SearchValidationError, match="invalid_date_range"):
        SearchFilters(date_from=date(2026, 9, 2), date_to=date(2026, 9, 1))


def test_cursor_is_signed_bound_to_actor_project_and_filters_and_is_stable(db_session, user_factory):
    world = _world(db_session, user_factory)
    filters = SearchFilters()
    first = service.project_search(
        db_session,
        project_id=world["project"].id,
        actor_user_id=world["owner"].id,
        filters=filters,
        limit=3,
    )
    assert first["next_cursor"]
    items = list(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        page = service.project_search(
            db_session,
            project_id=world["project"].id,
            actor_user_id=world["owner"].id,
            filters=filters,
            limit=3,
            cursor=cursor,
        )
        items.extend(page["items"])
        cursor = page["next_cursor"]
    keys = [(item["entity_type"], item["entity_id"]) for item in items]
    assert len(keys) == len(set(keys)) == 8

    for actor_id, changed_filters, cursor in (
        (world["second_member"].id, filters, first["next_cursor"]),
        (world["owner"].id, SearchFilters(query="changed"), first["next_cursor"]),
        (world["owner"].id, filters, first["next_cursor"][:-2] + "xx"),
    ):
        with pytest.raises(SearchValidationError, match="invalid_cursor"):
            service.project_search(
                db_session,
                project_id=world["project"].id,
                actor_user_id=actor_id,
                filters=changed_filters,
                cursor=cursor,
            )

    with pytest.raises(SearchValidationError, match="invalid_cursor"):
        service.project_search(
            db_session,
            project_id=world["other_project"].id,
            actor_user_id=world["owner"].id,
            filters=filters,
            cursor=first["next_cursor"],
        )


def test_scan_is_bounded_and_reports_truncation(db_session, user_factory, monkeypatch):
    world = _world(db_session, user_factory)
    for index in range(5):
        db_session.add(Document(
            project_id=world["project"].id,
            name=f"Bounded {index}.pdf",
            source="local_upload",
            status="ready",
        ))
    db_session.commit()
    monkeypatch.setattr(service, "MAX_SCAN_ROWS_PER_TYPE", 2)

    result = service.project_search(
        db_session,
        project_id=world["project"].id,
        actor_user_id=world["owner"].id,
        filters=SearchFilters(types=("document",)),
        limit=100,
    )
    assert result["scan_truncated"] is True
    assert result["scan_cap_per_type"] == 2
    assert len(result["items"]) == 2


def test_search_fails_closed_without_cursor_secret(db_session, user_factory, monkeypatch):
    world = _world(db_session, user_factory)
    first = service.project_search(
        db_session,
        project_id=world["project"].id,
        actor_user_id=world["owner"].id,
        filters=SearchFilters(),
        limit=1,
    )
    assert first["next_cursor"]
    monkeypatch.delenv("APP_SECRET_KEY")
    with pytest.raises(service.SearchUnavailable, match="search_cursor_secret_not_configured"):
        service.project_search(
            db_session,
            project_id=world["project"].id,
            actor_user_id=world["owner"].id,
            filters=SearchFilters(),
            cursor=first["next_cursor"],
        )
