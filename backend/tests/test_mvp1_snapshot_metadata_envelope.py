from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.api.workspace import _snapshot_path, _virtual_node_values, list_virtual_nodes
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot
from app.organizer_engine.types import DriveFile, FOLDER_MIME


def _file(identifier: str, name: str, parent: str, **changes) -> DriveFile:
    values = {
        "id": identifier,
        "name": name,
        "mime_type": "application/pdf",
        "parent_id": parent,
        "md5_checksum": "abc123",
        "size": 41,
        "modified_time": "2026-09-09T08:00:00Z",
        "provider": "google_drive",
        "parent_ids": (parent,),
        "provider_revision": "17",
        "web_url": f"https://drive.google.test/{identifier}",
        "availability": "available",
        "acl_state": "read",
        "provider_metadata": {"drive_id": "shared-drive"},
    }
    values.update(changes)
    return DriveFile(**values)


def test_snapshot_envelope_preserves_exact_metadata_and_virtual_path(db_session):
    organization = Organization(name="Synthetic tenant")
    db_session.add(organization); db_session.flush()
    project = Project(name="Synthetic project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    source = SourceFolder(project_id=project.id, external_id="root", name="Root", provider="google_drive")
    db_session.add(source); db_session.flush()
    snapshot = WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id, status="ready")
    db_session.add(snapshot); db_session.flush()

    root = _file("root", "Root", "account-root", mime_type=FOLDER_MIME, object_type="folder")
    folder = _file("folder", "Contracts", "root", mime_type=FOLDER_MIME, object_type="folder")
    document = _file("document", "Contract.pdf", "folder")
    objects = {item.id: item for item in (root, folder, document)}
    path = _snapshot_path(document, objects, root.id)
    node = VirtualNode(**_virtual_node_values(document, snapshot_id=snapshot.id, source_path=path))
    db_session.add(node); db_session.flush()

    assert node.external_id == "document"
    assert node.source_path == "/Contracts/Contract.pdf"
    assert node.parent_external_ids == ["folder"]
    assert node.provider_revision == "17"
    assert node.web_url.endswith("/document")
    assert node.availability == "available" and node.acl_state == "read"
    assert node.analysis_state == "pending"
    assert len(node.provider_metadata_hash) == 64


def test_missing_provider_values_are_explicit_unknown_not_invented():
    item = DriveFile("opaque", "unknown.bin", "application/octet-stream", "root")
    values = _virtual_node_values(item, snapshot_id=7, source_path="/unknown.bin")
    assert values["provider_revision"] is None
    assert values["availability"] == "unknown"
    assert values["acl_state"] == "unknown"
    assert values["web_url"] is None


def test_snapshot_metadata_migration_is_sequential_and_additive():
    backend = Path(__file__).resolve().parents[1]
    output = StringIO()
    config = Config(str(backend / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(backend / "migrations"))
    config.set_main_option("sqlalchemy.url", "postgresql://synthetic:synthetic@localhost/synthetic")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision("a54f001c0a23").down_revision == "a54f001c0a22"
    command.upgrade(config, "a54f001c0a22:a54f001c0a23", sql=True)
    sql = output.getvalue().lower()
    for token in (
        "source_path", "parent_external_ids", "provider_revision", "provider_metadata_hash",
        "web_url", "availability", "acl_state", "analysis_state",
    ):
        assert token in sql


def test_virtual_node_api_exposes_complete_safe_metadata_envelope(db_session, user_factory):
    organization = Organization(name="Synthetic API tenant")
    db_session.add(organization); db_session.flush()
    project = Project(name="Synthetic API project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    source = SourceFolder(project_id=project.id, external_id="root", name="Root", provider="google_drive")
    db_session.add(source); db_session.flush()
    snapshot = WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id, status="ready")
    db_session.add(snapshot); db_session.flush()
    item = _file(
        "shortcut", "Shortcut", "root", mime_type="application/vnd.google-apps.shortcut",
        shortcut_target_id="target", shortcut_target_mime_type=FOLDER_MIME,
        shortcut_target_resource_key="opaque-resource-key",
    )
    node = VirtualNode(**_virtual_node_values(item, snapshot_id=snapshot.id, source_path="/Shortcut"))
    db_session.add(node); db_session.commit()

    user = user_factory(is_admin=True)
    response = list_virtual_nodes(
        project_id=project.id, snapshot_id=snapshot.id, parent_external_id=None,
        cursor=0, limit=200, analysis_state="pending", db=db_session, user=user,
    )
    returned = response["nodes"][0]
    assert returned["parent_external_ids"] == ["root"]
    assert returned["provider_revision"] == "17"
    assert len(returned["provider_metadata_hash"]) == 64
    assert returned["web_url"].endswith("/shortcut")
    assert returned["shortcut_target_id"] == "target"
    assert returned["shortcut_target_mime_type"] == FOLDER_MIME
    assert returned["shortcut_target_resource_key"] == "opaque-resource-key"
