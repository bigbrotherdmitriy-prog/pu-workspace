"""Хранилище PU проходит тот же путь разбора папки (снимок), что Google Диск и Яндекс Диск."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.integrations.pu_storage import PuStorageAdapter
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PU_STORAGE_ROOT", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as db:
        org = Organization(name="PU pipeline org")
        db.add(org)
        db.flush()
        project = Project(name="P", organization_id=org.id)
        db.add(project)
        db.flush()
        source = SourceFolder(project_id=project.id, external_id="pu:/", name="Хранилище", provider="pu_storage")
        db.add(source)
        db.flush()
        snapshot = WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id, status="building")
        db.add(snapshot)
        db.flush()
        ids = (project.id, source.id, snapshot.id)
    yield {"sessions": sessions, "ids": ids}
    engine.dispose()


def test_snapshot_of_pu_storage_lists_files_and_folders(world, monkeypatch):
    from app.api import workspace

    pid, _sid, snap = world["ids"]
    adapter = PuStorageAdapter(pid)
    docs = adapter.create_folder("Договоры", "pu:/")
    adapter.write_file(docs, "Договор.pdf", b"%PDF-1.4 test")
    adapter.write_file("pu:/", "Письмо.txt", "Здравствуйте".encode())

    monkeypatch.setattr(workspace, "SessionLocal", world["sessions"])
    monkeypatch.setattr(workspace, "storage_for_project", lambda project_id, db, connection=None: PuStorageAdapter(project_id))
    workspace._build_snapshot(snap, pid, "pu:/", raise_errors=True)

    with world["sessions"]() as db:
        snapshot = db.get(WorkspaceSnapshot, snap)
        assert snapshot.status in {"ready", "built", "completed"}, snapshot.status
        names = sorted(n.name for n in db.scalars(select(VirtualNode).where(VirtualNode.snapshot_id == snap)))
    assert "Договор.pdf" in names and "Письмо.txt" in names and "Договоры" in names
