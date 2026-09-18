"""Opt-in PostgreSQL runtime proof for Snapshots; SQLite is not concurrency evidence.

These tests exercise the real app.api.workspace._build_snapshot implementation
(not a re-implementation of its logic) against a real PostgreSQL schema, to
prove the invariants that offline/SQLite tests structurally cannot prove:

- a concurrent publish of the same snapshot has exactly one winner, and the
  loser is a clean no-op rather than a false "failed"/"dead_letter";
- an already-published VirtualNode is never rewritten by a racing publisher;
- a pre-migration VirtualNode row is correctly backfilled by the new Snapshot
  metadata-envelope migrations.
"""
from pathlib import Path
from threading import Barrier, Lock, Thread
import os
import time
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register all mapped tables
from app.core.integration_types import StorageObject
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot


def safe_url() -> str:
    value = os.getenv("PUW_MVP1_SNAPSHOT_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: PUW_MVP1_SNAPSHOT_DATABASE_URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_mvp1_test_") and not parsed.query
    return value


class FakeStorage:
    """A provider stub slow enough that two racing publishers overlap in the
    provider-read window (walk_tree), which is exactly where app.api.workspace
    ._build_snapshot deliberately holds no DB lock."""

    provider = "google_drive"

    def __init__(self, root_id: str, child_count: int, *, delay: float = 0.2):
        self._delay = delay
        self.root = StorageObject(root_id, "Root", "application/vnd.google-apps.folder", "account-root",
                                   object_type="folder", provider=self.provider)
        self.children = [
            StorageObject(f"{root_id}-child-{i}", f"Child {i}", "application/pdf", root_id,
                          md5_checksum=f"checksum-{i}", size=10 + i, provider=self.provider)
            for i in range(child_count)
        ]

    def get_object(self, object_id):
        return self.root

    def walk_tree(self, root_folder_id):
        time.sleep(self._delay)  # widen the race window past the DB round-trip
        return list(self.children)


def _setup_schema_engine(url: str):
    schema = "mvp1_snapshot_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, hide_parameters=True, connect_args={
        "connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000",
    })
    return schema, admin, engine


def test_postgres_concurrent_snapshot_publish_has_exactly_one_winner(monkeypatch):
    url = safe_url()
    schema, admin, engine = _setup_schema_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as db:
            org = Organization(name="Synthetic snapshot tenant")
            db.add(org); db.flush()
            project = Project(name="Synthetic snapshot project", organization_id=org.id)
            db.add(project); db.flush()
            source = SourceFolder(project_id=project.id, external_id="root", name="Root", provider="google_drive")
            db.add(source); db.flush()
            snapshot = WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id, status="building")
            db.add(snapshot); db.flush()
            ids = (project.id, source.id, snapshot.id)

        adapter = FakeStorage("root", child_count=5, delay=0.2)
        from app.api import workspace
        monkeypatch.setattr(workspace, "SessionLocal", sessions)
        monkeypatch.setattr(workspace, "storage_for_project", lambda project_id, db: adapter)

        barrier = Barrier(2)
        lock = Lock()
        outcomes = []

        def contender():
            barrier.wait(5)
            try:
                workspace._build_snapshot(ids[2], ids[0], "root", raise_errors=True)
                outcome = "returned"
            except Exception as exc:  # pragma: no cover - only on genuine failure
                outcome = f"raised:{exc!r}"
            with lock:
                outcomes.append(outcome)

        threads = [Thread(target=contender) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert not any(thread.is_alive() for thread in threads)
        # Neither contender should raise: the loser must see the winner's
        # "ready" status under the row lock and return cleanly, not crash into
        # a unique-constraint violation or a false "failed" state.
        assert outcomes == ["returned", "returned"]

        with sessions() as db:
            published = db.get(WorkspaceSnapshot, ids[2])
            assert published.status == "ready"
            assert published.item_count == 6  # root + 5 children
            nodes = list(db.scalars(select(VirtualNode).where(VirtualNode.snapshot_id == ids[2])))
            # Exactly one full set of nodes: no duplicate insert from the loser.
            assert len(nodes) == 6
            assert len({node.external_id for node in nodes}) == 6
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_completed_virtual_node_is_never_rewritten_under_a_race(monkeypatch):
    """A snapshot already 'ready' must be a pure no-op for a racing publisher,
    even under real overlapping PostgreSQL transactions, not just the
    single-threaded SQLite case."""
    url = safe_url()
    schema, admin, engine = _setup_schema_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as db:
            org = Organization(name="Synthetic immutability tenant")
            db.add(org); db.flush()
            project = Project(name="Synthetic immutability project", organization_id=org.id)
            db.add(project); db.flush()
            source = SourceFolder(project_id=project.id, external_id="root", name="Root", provider="google_drive")
            db.add(source); db.flush()
            snapshot = WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id, status="building")
            db.add(snapshot); db.flush()
            ids = (project.id, source.id, snapshot.id)

        from app.api import workspace
        monkeypatch.setattr(workspace, "SessionLocal", sessions)

        first_adapter = FakeStorage("root", child_count=3, delay=0.0)
        monkeypatch.setattr(workspace, "storage_for_project", lambda project_id, db: first_adapter)
        workspace._build_snapshot(ids[2], ids[0], "root", raise_errors=True)

        with sessions() as db:
            published = {
                node.external_id: (node.id, node.name, node.checksum, node.provider_metadata_hash)
                for node in db.scalars(select(VirtualNode).where(VirtualNode.snapshot_id == ids[2]))
            }
        assert published

        # A stray re-dispatch races against the already-ready snapshot, backed
        # by a slow adapter that returns *different* metadata (as if the
        # source changed upstream) to prove nothing gets overwritten.
        mutated_adapter = FakeStorage("root", child_count=3, delay=0.2)
        for child in mutated_adapter.children:
            child.name = "Renamed after publish"
            child.md5_checksum = "checksum-after-publish"
        monkeypatch.setattr(workspace, "storage_for_project", lambda project_id, db: mutated_adapter)
        workspace._build_snapshot(ids[2], ids[0], "root", raise_errors=True)

        with sessions() as db:
            after = {
                node.external_id: (node.id, node.name, node.checksum, node.provider_metadata_hash)
                for node in db.scalars(select(VirtualNode).where(VirtualNode.snapshot_id == ids[2]))
            }
        assert after == published
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_existing_virtual_node_is_backfilled_by_snapshot_migrations(monkeypatch):
    """A VirtualNode row written before the Snapshot metadata-envelope
    migrations must survive the upgrade with the documented backfill defaults,
    not be dropped, duplicated or left with NULLs in NOT NULL columns.

    Requires an explicitly empty database (same convention as the v5.4
    materialization migration proof): the full migration history is applied
    from scratch, so pre-existing tables/data would collide.
    """
    url = safe_url()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        assert not inspect(engine).get_table_names(), "Refuse nonempty test database"
        backend = Path(__file__).resolve().parents[1]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "migrations"))
        monkeypatch.setenv("DATABASE_URL", url)

        # Land on the pre-Snapshot head first, with real pre-migration data.
        # IDs are never hardcoded: earlier migrations in the chain already
        # seed a default organization row, so this must not assume id=1 is free.
        command.upgrade(config, "e16a1c2d3f40")
        with engine.begin() as connection:
            org_id = connection.execute(text(
                "INSERT INTO organizations (name) VALUES ('Synthetic backfill tenant') RETURNING id"
            )).scalar_one()
            project_id = connection.execute(text(
                "INSERT INTO projects (name, organization_id) VALUES ('Synthetic backfill project', :org_id) "
                "RETURNING id"
            ), {"org_id": org_id}).scalar_one()
            source_id = connection.execute(text(
                "INSERT INTO source_folders (project_id, external_id, name, provider) "
                "VALUES (:project_id, 'root', 'Root', 'google_drive') RETURNING id"
            ), {"project_id": project_id}).scalar_one()
            snapshot_id = connection.execute(text(
                "INSERT INTO workspace_snapshots (project_id, source_folder_id, status) "
                "VALUES (:project_id, :source_id, 'ready') RETURNING id"
            ), {"project_id": project_id, "source_id": source_id}).scalar_one()
            node_id = connection.execute(text(
                "INSERT INTO virtual_nodes (snapshot_id, external_id, name, mime_type, node_type) "
                "VALUES (:snapshot_id, 'pre-migration-node', 'Pre-migration node', 'application/pdf', 'file') "
                "RETURNING id"
            ), {"snapshot_id": snapshot_id}).scalar_one()

        # Now apply the Snapshot metadata-envelope migrations on top.
        command.upgrade(config, "201286e2acd0")

        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT external_id, name, parent_external_ids, provider_metadata_hash, "
                "availability, acl_state, analysis_state FROM virtual_nodes WHERE id = :node_id"
            ), {"node_id": node_id}).mappings().one()
            assert row["external_id"] == "pre-migration-node"
            assert row["name"] == "Pre-migration node"  # untouched original data
            assert list(row["parent_external_ids"]) == []
            assert row["provider_metadata_hash"] == "unknown"
            assert row["availability"] == "unknown"
            assert row["acl_state"] == "unknown"
            assert row["analysis_state"] == "pending"
            count = connection.execute(text(
                "SELECT count(*) FROM virtual_nodes WHERE snapshot_id = :snapshot_id"
            ), {"snapshot_id": snapshot_id}).scalar()
            assert count == 1  # no duplication from the migration
    finally:
        engine.dispose()
