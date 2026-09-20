import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.management import management_history
from app.api.saved_search_views import (
    SavedSearchFilters,
    SavedSearchViewCreate,
    SavedSearchViewUpdate,
    create_saved_search_view,
    delete_saved_search_view,
    list_saved_search_views,
    update_saved_search_view,
)
from app.models.management import ManagementHistory
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.saved_search_view import SavedSearchView


def _world(db, user_factory):
    organization = Organization(name="Saved view tenant")
    other_organization = Organization(name="Other saved view tenant")
    db.add_all([organization, other_organization]); db.flush()
    owner = user_factory(name="View owner")
    peer = user_factory(name="View peer")
    outsider = user_factory(name="View outsider")
    admin = user_factory(name="Unscoped admin", is_admin=True)
    project = Project(name="Alpha", organization_id=organization.id)
    second_project = Project(name="Beta", organization_id=organization.id)
    foreign_project = Project(name="Foreign", organization_id=other_organization.id)
    db.add_all([project, second_project, foreign_project]); db.flush()
    db.add_all([
        ProjectMember(project_id=project.id, user_id=owner.id, role="viewer"),
        ProjectMember(project_id=project.id, user_id=peer.id, role="viewer"),
        ProjectMember(project_id=second_project.id, user_id=owner.id, role="viewer"),
        ProjectMember(project_id=foreign_project.id, user_id=outsider.id, role="viewer"),
    ])
    db.commit()
    return owner, peer, outsider, admin, project, second_project, foreign_project


def _create(db, owner, project, *, name="Мои просроченные", q="бетон"):
    return create_saved_search_view(SavedSearchViewCreate(
        project_id=project.id,
        name=name,
        filters=SavedSearchFilters(
            q=q, types=["task", "obligation"], contract_id=7,
            counterparty="Строй Подряд", date_from="2026-09-01", date_to="2026-09-30",
        ),
    ), db, owner)


def test_saved_view_crud_is_versioned_soft_deleted_and_audited_without_private_values(
    db_session, user_factory,
):
    owner, _, _, _, project, *_ = _world(db_session, user_factory)
    created = _create(db_session, owner, project)
    assert created["record_version"] == 1
    assert created["filters"]["q"] == "бетон"
    assert list_saved_search_views(project.id, db_session, owner)["views"] == [created]

    updated = update_saved_search_view(created["id"], SavedSearchViewUpdate(
        expected_record_version=1,
        name="Контроль оплат",
        filters=SavedSearchFilters(q="оплата", types=["contract"]),
    ), db_session, owner)
    assert updated["record_version"] == 2 and updated["name"] == "Контроль оплат"

    deleted = delete_saved_search_view(created["id"], 2, db_session, owner)
    assert deleted == {"id": created["id"], "record_version": 3, "deleted": True}
    assert list_saved_search_views(project.id, db_session, owner)["views"] == []
    stored = db_session.get(SavedSearchView, created["id"])
    assert stored.deleted_at is not None and stored.record_version == 3

    history = list(db_session.scalars(select(ManagementHistory).where(
        ManagementHistory.entity_type == "saved_search_view",
        ManagementHistory.entity_id == created["id"],
    ).order_by(ManagementHistory.id)))
    assert [row.action for row in history] == ["created", "updated", "deleted"]
    serialized = str([(row.old_values, row.new_values, row.evidence, row.reason) for row in history])
    assert "бетон" not in serialized and "оплата" not in serialized and "Строй Подряд" not in serialized
    assert "filter_keys" in serialized and "filter_fingerprint" in serialized

    # Soft deletion frees the active-name namespace without erasing history.
    replacement = _create(db_session, owner, project, name="Контроль оплат", q="новый")
    assert replacement["id"] != created["id"]


def test_owner_isolation_and_generic_history_privacy(db_session, user_factory):
    owner, peer, *_rest, project, _second, _foreign = _world(db_session, user_factory)
    created = _create(db_session, owner, project)
    assert list_saved_search_views(project.id, db_session, peer)["views"] == []
    with pytest.raises(HTTPException) as hidden:
        update_saved_search_view(created["id"], SavedSearchViewUpdate(
            expected_record_version=1, name="Украденное представление",
        ), db_session, peer)
    assert hidden.value.status_code == 404
    with pytest.raises(HTTPException) as history_hidden:
        management_history("saved_search_view", created["id"], project.id, db_session, peer)
    assert history_hidden.value.status_code == 404
    own_history = management_history("saved_search_view", created["id"], project.id, db_session, owner)
    assert len(own_history["history"]) == 1
    assert "бетон" not in str(own_history)


def test_exact_project_membership_is_required_even_for_admin(db_session, user_factory):
    owner, _, outsider, admin, project, _, foreign_project = _world(db_session, user_factory)
    _create(db_session, owner, project)
    for actor in (outsider, admin):
        with pytest.raises(HTTPException) as denied:
            list_saved_search_views(project.id, db_session, actor)
        assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as cross_tenant:
        create_saved_search_view(SavedSearchViewCreate(
            project_id=foreign_project.id,
            name="Foreign",
            filters=SavedSearchFilters(q="x"),
        ), db_session, owner)
    assert cross_tenant.value.status_code == 403


def test_stale_cas_and_duplicate_active_name_fail_closed(db_session, user_factory):
    owner, _, _, _, project, *_ = _world(db_session, user_factory)
    created = _create(db_session, owner, project)
    update_saved_search_view(created["id"], SavedSearchViewUpdate(
        expected_record_version=1, name="Новая версия",
    ), db_session, owner)
    with pytest.raises(HTTPException) as stale:
        update_saved_search_view(created["id"], SavedSearchViewUpdate(
            expected_record_version=1, name="Потерянное обновление",
        ), db_session, owner)
    assert stale.value.status_code == 409
    assert stale.value.detail == {"code": "record_version_conflict", "expected": 1, "actual": 2}

    with pytest.raises(HTTPException) as duplicate:
        _create(db_session, owner, project, name="Новая версия", q="другое")
    assert duplicate.value.status_code == 409
    assert db_session.query(ManagementHistory).filter_by(entity_type="saved_search_view").count() == 2


@pytest.mark.parametrize("filters", [
    {"types": ["task", "task"]},
    {"types": ["secret"]},
    {"date_from": "2026-10-01", "date_to": "2026-09-01"},
    {"unknown": "value"},
])
def test_filter_contract_is_allowlisted(filters):
    with pytest.raises(ValidationError):
        SavedSearchFilters.model_validate(filters)


def test_router_contract_paths_are_stable():
    from app.api.saved_search_views import router

    contracts = {(route.path, method) for route in router.routes for method in route.methods}
    assert ("/saved-search-views", "GET") in contracts
    assert ("/saved-search-views", "POST") in contracts
    assert ("/saved-search-views/{view_id}", "PATCH") in contracts
    assert ("/saved-search-views/{view_id}", "DELETE") in contracts
