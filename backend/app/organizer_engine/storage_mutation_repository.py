"""Read-only DB resolver for IDs-only storage-mutation jobs."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.drive_connection import DriveConnection
from app.models.organizer import OrganizerAction, OrganizerProposal, OrganizerSession
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot
from app.organizer_engine.storage_mutations import (
    MutationCommand, MutationConflict, StorageBindingPin, StorageMutation,
)


def _canonical(provider: str) -> str:
    return "google_drive" if provider == "google_workspace" else provider


class StorageMutationResolver:
    """Resolve persisted IDs to exact pins; never calls a provider or commits."""

    def __init__(self, db: Session):
        self.db = db

    def resolve(self, payload: dict) -> MutationCommand:
        project_id = int(payload["project_id"])
        proposal = self.db.scalar(select(OrganizerProposal).where(
            OrganizerProposal.id == int(payload["proposal_id"]),
            OrganizerProposal.project_id == project_id,
        ).with_for_update())
        action = self.db.scalar(select(OrganizerAction).where(
            OrganizerAction.id == int(payload["action_id"]),
            OrganizerAction.proposal_id == int(payload["proposal_id"]),
        ))
        if proposal is None or action is None or action.user_decision not in {"approved", "edited"}:
            raise MutationConflict("resource_unavailable")
        if not proposal.copy_folder_id.startswith("virtual:"):
            raise MutationConflict("exact_snapshot_required")
        snapshot_id = int(proposal.copy_folder_id.removeprefix("virtual:"))
        snapshot = self.db.scalar(select(WorkspaceSnapshot).where(
            WorkspaceSnapshot.id == snapshot_id,
            WorkspaceSnapshot.project_id == project_id,
            WorkspaceSnapshot.status == "ready",
        ))
        session = self.db.scalar(select(OrganizerSession).where(
            OrganizerSession.id == proposal.session_id,
            OrganizerSession.project_id == project_id,
        ))
        connection = self.db.scalar(select(DriveConnection).where(
            DriveConnection.project_id == project_id,
            DriveConnection.status == "connected",
        ))
        if snapshot is None or session is None or connection is None or not connection.connection_id:
            raise MutationConflict("resource_unavailable")
        source = self.db.get(SourceFolder, snapshot.source_folder_id)
        pinned = (snapshot.analysis_result or {}).get("storage_binding")
        expected = {
            "project_id": project_id,
            "provider": connection.provider,
            "connection_id": connection.connection_id,
            "connection_row_id": connection.id,
            "folder_id": source.external_id if source else None,
        }
        if (source is None or source.project_id != project_id or pinned != expected
                or _canonical(source.provider) != _canonical(connection.provider)
                or session.source_folder_id != source.external_id):
            raise MutationConflict("stale_or_cross_project_binding")
        node = self.db.scalar(select(VirtualNode).where(
            VirtualNode.snapshot_id == snapshot.id,
            VirtualNode.external_id == action.file_id,
        ))
        target_name = action.edited_name or action.proposed_name
        target_folder_name = action.edited_folder or action.target_folder
        target = self.db.scalar(select(VirtualNode).where(
            VirtualNode.snapshot_id == snapshot.id,
            VirtualNode.node_type == "folder",
            VirtualNode.name == target_folder_name,
        ))
        if node is None or target is None or not node.parent_external_id:
            raise MutationConflict("exact_target_unavailable")
        revision = node.checksum or (node.source_modified_at.isoformat() if node.source_modified_at else None)
        if not revision:
            raise MutationConflict("source_revision_unavailable")
        kind = "rename" if node.name != target_name else "move"
        return MutationCommand(
            command_key=str(payload["command_key"]),
            pin=StorageBindingPin(
                project_id=project_id,
                provider=_canonical(connection.provider),
                connection_id=connection.connection_id,
                folder_id=source.external_id,
                binding_version=connection.id,
            ),
            expected_record_version=int(payload["expected_record_version"]),
            operations=(StorageMutation(
                kind=kind,
                object_id=node.external_id,
                source_revision=revision,
                old_parent_id=node.parent_external_id,
                old_name=node.name,
                new_parent_id=target.external_id,
                new_name=target_name,
            ),),
        )
