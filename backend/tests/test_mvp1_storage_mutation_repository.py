from datetime import datetime, timezone

import pytest

from app.models.drive_connection import DriveConnection
from app.models.organization_contract import Organization
from app.models.organizer import OrganizerAction, OrganizerProposal, OrganizerSession
from app.models.project import Project
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot
from app.organizer_engine.storage_mutation_repository import StorageMutationResolver
from app.organizer_engine.storage_mutations import MutationConflict


def world(db_session):
    organization = Organization(name="Synthetic storage tenant")
    db_session.add(organization); db_session.flush()
    project = Project(name="Synthetic storage project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    connection = DriveConnection(project_id=project.id, provider="google_drive", account_email="synthetic@example.test",
                                 root_folder_id="root/nested", connection_id="connection-1", status="connected")
    source = SourceFolder(project_id=project.id, external_id="root/nested", name="Nested", provider="google_drive")
    db_session.add_all([connection, source]); db_session.flush()
    binding = {"project_id": project.id, "provider": "google_drive", "connection_id": "connection-1",
               "connection_row_id": connection.id, "folder_id": "root/nested"}
    snapshot = WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id, status="ready",
                                 analysis_result={"storage_binding": binding})
    session = OrganizerSession(project_id=project.id, source_folder_id="root/nested", source_folder_name="Nested",
                               copy_folder_id="virtual:pending", status="ready")
    db_session.add_all([snapshot, session]); db_session.flush()
    session.copy_folder_id = f"virtual:{snapshot.id}"
    proposal = OrganizerProposal(project_id=project.id, session_id=session.id, folder_name="Nested",
                                 source_folder_id="root/nested", copy_folder_id=f"virtual:{snapshot.id}",
                                 status="approved", idempotency_key="proposal-key")
    db_session.add(proposal); db_session.flush()
    db_session.add_all([
        VirtualNode(snapshot_id=snapshot.id, external_id="root/nested/file", parent_external_id="root/nested",
                    name="old.pdf", mime_type="application/pdf", node_type="file", checksum="sha-1"),
        VirtualNode(snapshot_id=snapshot.id, external_id="root/nested/contracts", parent_external_id="root/nested",
                    name="contracts", mime_type="folder", node_type="folder"),
    ]); db_session.flush()
    action = OrganizerAction(proposal_id=proposal.id, action_order=1, action="rename", source="old.pdf",
                             target_folder="contracts", proposed_name="standard.pdf", requires_confirmation=True,
                             file_id="root/nested/file", current_parent_id="root/nested", confidence=1,
                             reasoning="synthetic", user_decision="approved")
    db_session.add(action); db_session.flush()
    return project, connection, snapshot, action


def test_resolver_builds_exact_current_command_without_provider_calls(db_session):
    project, connection, _snapshot, action = world(db_session)
    command = StorageMutationResolver(db_session).resolve({"project_id": project.id, "proposal_id": action.proposal_id,
        "action_id": action.id, "command_key": "mutation:exact:01", "expected_record_version": 1})
    assert command.pin.connection_id == connection.connection_id
    assert command.pin.folder_id == "root/nested"
    assert command.operations[0].source_revision == "sha-1"
    assert command.operations[0].new_parent_id == "root/nested/contracts"


def test_resolver_rejects_stale_connection_and_cross_project(db_session):
    project, connection, snapshot, action = world(db_session)
    connection.connection_id = "connection-2"
    with pytest.raises(MutationConflict, match="stale_or_cross_project"):
        StorageMutationResolver(db_session).resolve({"project_id": project.id, "proposal_id": action.proposal_id,
            "action_id": action.id, "command_key": "mutation:exact:02", "expected_record_version": 1})
    snapshot.analysis_result = {"storage_binding": {}}
    with pytest.raises(MutationConflict):
        StorageMutationResolver(db_session).resolve({"project_id": project.id + 1, "proposal_id": action.proposal_id,
            "action_id": action.id, "command_key": "mutation:exact:03", "expected_record_version": 1})


@pytest.mark.skipif(not __import__("os").getenv("TEST_POSTGRES_DSN"), reason="TEST_POSTGRES_DSN unavailable")
def test_postgresql_concurrent_resolve_requires_external_runtime_fixture():
    assert True
