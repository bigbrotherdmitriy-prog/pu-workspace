from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.models.document import Document
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.mvp3.search import (
    SearchDenied,
    SearchFilters,
    SearchValidationError,
    create_saved_view,
    delete_saved_view,
    get_saved_view_history,
    list_saved_views,
    project_search,
    update_saved_view,
)


def _project(db, organization, user, name="Северный участок", role="viewer"):
    row = Project(name=name, organization_id=organization.id)
    db.add(row)
    db.flush()
    db.add(ProjectMember(project_id=row.id, user_id=user.id, role=role))
    db.flush()
    return row


def _task(db, project, user, *, title, source_file_id):
    row = Task(
        project_id=project.id,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        title=title,
        status="assigned",
        priority="normal",
        due_date=date(2026, 9, 20),
        source_type="manual",
        source_file_id=source_file_id,
        source_file_name="Источник.pdf",
        source_excerpt="synthetic fixture",
        source_excerpt_hash=(source_file_id * 64)[:64],
        confidence=1.0,
        needs_review=False,
    )
    db.add(row)
    db.flush()
    return row


def _fixture(db_session, user_factory):
    owner = user_factory()
    outsider = user_factory()
    organization = Organization(name="Synthetic tenant")
    other_organization = Organization(name="Other synthetic tenant")
    db_session.add_all([organization, other_organization])
    db_session.flush()
    project = _project(db_session, organization, owner, role="manager")
    other_project = _project(db_session, other_organization, outsider, name="Чужой проект")
    contract = Contract(
        project_id=project.id,
        number="Д-42",
        title="Монтаж фасада",
        counterparty="ООО Синтетика",
        signed_at=date(2026, 8, 15),
    )
    other_contract = Contract(
        project_id=other_project.id,
        number="SECRET-1",
        title="Недоступный договор",
        counterparty="Чужая компания",
    )
    db_session.add_all([contract, other_contract])
    db_session.flush()
    docs = [
        Document(project_id=project.id, name="Договор Д-42.pdf", source="local_upload", status="ready",
                 source_modified_at=datetime(2026, 9, 3, 10, tzinfo=timezone.utc)),
        Document(project_id=project.id, name="Акт фасад.pdf", source="local_upload", status="ready",
                 source_modified_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc)),
        Document(project_id=other_project.id, name="SECRET payroll.pdf", source="local_upload", status="ready",
                 source_modified_at=datetime(2026, 9, 4, 10, tzinfo=timezone.utc)),
    ]
    db_session.add_all(docs)
    db_session.flush()
    task = _task(db_session, project, owner, title="Проверить договор Д-42", source_file_id="task-a")
    _task(db_session, other_project, outsider, title="SECRET internal", source_file_id="task-b")
    db_session.commit()
    return owner, outsider, organization, project, contract, docs, task


def test_search_is_tenant_project_and_permission_scoped(db_session, user_factory):
    owner, outsider, organization, project, *_ = _fixture(db_session, user_factory)
    result = project_search(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        filters=SearchFilters(query="договор"),
        limit=25,
    )
    assert result["items"]
    assert {item["project"]["id"] for item in result["items"]} == {project.id}
    assert all("SECRET" not in item["name"] for item in result["items"])

    with pytest.raises(SearchDenied):
        project_search(
            db_session,
            organization_id=organization.id,
            project_id=project.id,
            actor_user_id=outsider.id,
            filters=SearchFilters(),
        )


def test_search_uses_bound_literals_and_allowlisted_types(db_session, user_factory):
    owner, _, organization, project, *_ = _fixture(db_session, user_factory)
    attack = project_search(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        filters=SearchFilters(query="%' OR 1=1 --"),
    )
    assert attack["items"] == []
    with pytest.raises(SearchValidationError):
        SearchFilters(types=("document", "raw_sql"))
    with pytest.raises(SearchValidationError):
        SearchFilters(query="x" * 201)
    with pytest.raises(SearchValidationError, match="invalid_limit"):
        project_search(
            db_session,
            organization_id=organization.id,
            project_id=project.id,
            actor_user_id=owner.id,
            filters=SearchFilters(),
            limit=101,
        )


def test_search_filters_contract_counterparty_date_and_returns_explainable_links(db_session, user_factory):
    owner, _, organization, project, contract, *_ = _fixture(db_session, user_factory)
    result = project_search(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        filters=SearchFilters(
            types=("contract",),
            contract_id=contract.id,
            counterparty="синтетика",
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
        ),
    )
    assert [(item["entity_type"], item["entity_id"]) for item in result["items"]] == [
        ("contract", contract.id)
    ]
    assert result["items"][0]["links"] == [
        {"relation": "entity", "href": f"/projects/{project.id}/contracts/{contract.id}"}
    ]
    assert "notes" not in result["items"][0]


def test_cursor_is_stable_bound_to_query_and_has_no_duplicates(db_session, user_factory):
    owner, _, organization, project, *_ = _fixture(db_session, user_factory)
    first = project_search(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        filters=SearchFilters(),
        limit=2,
    )
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = project_search(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        filters=SearchFilters(),
        limit=2,
        cursor=first["next_cursor"],
    )
    keys = [(item["entity_type"], item["entity_id"]) for item in first["items"] + second["items"]]
    assert len(keys) == len(set(keys))

    with pytest.raises(SearchValidationError):
        project_search(
            db_session,
            organization_id=organization.id,
            project_id=project.id,
            actor_user_id=owner.id,
            filters=SearchFilters(query="changed"),
            cursor=first["next_cursor"],
        )
    with pytest.raises(SearchValidationError):
        project_search(
            db_session,
            organization_id=organization.id,
            project_id=project.id,
            actor_user_id=owner.id,
            filters=SearchFilters(),
            cursor=first["next_cursor"][:-2] + "xx",
        )


def test_saved_views_are_owner_scoped_cas_and_append_only(db_session, user_factory):
    owner, outsider, organization, project, contract, *_ = _fixture(db_session, user_factory)
    view = create_saved_view(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        name="Договоры контрагента",
        filters=SearchFilters(types=("contract",), contract_id=contract.id),
    )
    db_session.commit()
    assert view.record_version == 1
    assert [row.id for row in list_saved_views(
        db_session, organization_id=organization.id, project_id=project.id, actor_user_id=owner.id
    )] == [view.id]
    assert list_saved_views(
        db_session, organization_id=organization.id, project_id=project.id, actor_user_id=outsider.id
    ) == []

    updated = update_saved_view(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        view_id=view.id,
        expected_version=1,
        name="Только договоры",
        filters=SearchFilters(types=("contract",)),
    )
    db_session.commit()
    assert updated.record_version == 2
    with pytest.raises(SearchValidationError, match="version_conflict"):
        update_saved_view(
            db_session,
            organization_id=organization.id,
            project_id=project.id,
            actor_user_id=owner.id,
            view_id=view.id,
            expected_version=1,
            name="stale",
            filters=SearchFilters(),
        )
    with pytest.raises(SearchDenied):
        update_saved_view(
            db_session,
            organization_id=organization.id,
            project_id=project.id,
            actor_user_id=outsider.id,
            view_id=view.id,
            expected_version=2,
            name="takeover",
            filters=SearchFilters(),
        )

    deleted = delete_saved_view(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        view_id=view.id,
        expected_version=2,
    )
    db_session.commit()
    assert deleted.state == "deleted"
    history = get_saved_view_history(
        db_session,
        organization_id=organization.id,
        project_id=project.id,
        actor_user_id=owner.id,
        view_id=view.id,
    )
    assert [(row.sequence, row.event, row.resulting_version) for row in history] == [
        (1, "created", 1), (2, "updated", 2), (3, "deleted", 3)
    ]
    history[0].event = "tampered"
    with pytest.raises(ValueError, match="saved_view_history_is_append_only"):
        db_session.flush()


def test_saved_view_rejects_unknown_filter_keys_and_raw_sql(db_session, user_factory):
    owner, _, organization, project, *_ = _fixture(db_session, user_factory)
    with pytest.raises(SearchValidationError):
        SearchFilters.from_mapping({"types": ["contract"], "sql": "DROP TABLE contracts"})
    with pytest.raises(SearchValidationError):
        SearchFilters.from_mapping({"query": {"$where": "1=1"}})
    assert list_saved_views(
        db_session, organization_id=organization.id, project_id=project.id, actor_user_id=owner.id
    ) == []
