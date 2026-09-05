from types import SimpleNamespace
from contextlib import contextmanager
import json
from urllib.parse import quote

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.api import projects, workspace
from app.core.auth import require_user
from app.core.integration_types import StorageObject
from app.database import Base, get_db
from app.integrations import storage
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.jobs import handlers, queue
from app.models.drive_connection import DriveConnection
from app.models.audit_log import AuditLog
from app.models.integration_credential import IntegrationCredential
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.organizer import OrganizerSession
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot


class FakeStorage:
    supports_managed_copy_cleanup = True

    def __init__(self, provider):
        self.provider = provider
        self.root = 'disk:/' if provider == 'yandex_disk' else 'root'
        self.ids = (['disk:/Customer', 'disk:/Customer/Phase', 'disk:/Customer/Phase/Project #1']
                    if provider == 'yandex_disk' else ['opaque-A', 'opaque-B', 'opaque-C'])
        self.other = 'disk:/Other/Project #1' if provider == 'yandex_disk' else 'opaque-D'
        self.items = {self.root: self.folder(self.root, 'Disk', '')}
        parent = self.root
        for identifier, name in zip(self.ids, ['Customer', 'Phase', 'Project #1']):
            self.items[identifier] = self.folder(identifier, name, parent)
            parent = identifier
        self.items[self.other] = self.folder(self.other, 'Project #1', self.root)
        self.calls = []
        self.error = None
        self.trashed: set[str] = set()
        self.trash_calls: list[str] = []
        self.crash_after_trash_once = False

    def folder(self, identifier, name, parent):
        return StorageObject(identifier, name, 'inode/directory', parent, provider=self.provider)

    def get_object(self, identifier):
        self.calls.append(identifier)
        if self.error:
            raise self.error
        return self.items[identifier]

    def list_children(self, identifier):
        return [item for item in self.items.values() if item.parent_id == identifier]

    def walk_tree(self, identifier):
        return self.list_children(identifier)

    def read_bytes(self, identifier, max_bytes):
        item = self.items[identifier]
        return b'Synthetic contract C-100 dated 2026-09-05 amount 1000', item.mime_type

    def trash_safe_copy(self, identifier):
        self.trash_calls.append(identifier)
        self.trashed.add(identifier)
        if self.crash_after_trash_once:
            self.crash_after_trash_once = False
            raise RuntimeError('synthetic crash after provider effect')


@pytest.fixture(params=['google_drive', 'yandex_disk'])
def bound(tmp_path, monkeypatch, request):
    from app.organizer_engine import managed_copies as lifecycle
    engine = create_engine(f'sqlite:///{tmp_path / "binding.sqlite"}', connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def functions(connection, _):
        connection.create_function('now', 0, lambda: '2026-09-03 12:00:00')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    adapter = FakeStorage(request.param)
    # The cleanup tests below inject this synthetic adapter instead of credentials.
    monkeypatch.setattr(lifecycle, '_require_cleanup_capability', lambda provider: None)
    with factory() as db:
        org = Organization(name='Synthetic owner')
        owner = User(name='Owner', email='owner@example.test')
        outsider = User(name='Other', email='other@example.test')
        db.add_all([org, owner, outsider]); db.flush()
        old = Project(name='Persistent Project', organization_id=org.id)
        new = Project(name='New project', organization_id=org.id)
        db.add_all([old, new]); db.flush()
        db.add_all([ProjectMember(project_id=p.id, user_id=owner.id, role='owner') for p in [old, new]])
        conn = DriveConnection(project_id=new.id, provider=request.param, connection_id='chosen-account',
                               account_email='synthetic@example.test', root_folder_id=adapter.root)
        db.add(conn); db.commit()
        ids = (old.id, new.id, owner.id, outsider.id, conn.id)
    def get_session():
        with factory() as db:
            yield db
    def user(x_user: str = Header(default='owner')):
        with factory() as db:
            return db.get(User, ids[2] if x_user == 'owner' else ids[3])
    def resolve(project_id, db):
        assert project_id == ids[1], 'Must not resolve Persistent Project storage'
        return adapter
    monkeypatch.setattr(workspace, 'SessionLocal', factory)
    monkeypatch.setattr(workspace, 'storage_for_project', resolve)
    # SQLite create_all lacks PostgreSQL migration server defaults for raw INSERT.
    def create_session(repo, project_id, source_folder_id, source_folder_name):
        row = OrganizerSession(project_id=project_id, source_folder_id=source_folder_id, source_folder_name=source_folder_name)
        repo.db.add(row); repo.db.flush()
        return row.id
    monkeypatch.setattr(workspace.OrganizerRepository, 'create_session', create_session)
    app = FastAPI()
    app.include_router(projects.router); app.include_router(workspace.router)
    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[require_user] = user
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=factory, adapter=adapter, old=ids[0], new=ids[1], connection=ids[4])
    engine.dispose()


def choose(bound, folder=None, **kwargs):
    folder = folder or bound.adapter.ids[-1]
    return bound.client.post(f'/projects/{bound.new}/source-folders/{quote(folder, safe="")}/snapshot-queue', **kwargs)


def test_confirm_persists_exact_binding_and_returns_job(bound):
    response = choose(bound)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['project_id'] == bound.new
    assert result['provider'] == bound.adapter.provider
    assert result['connection_id'] == 'chosen-account'
    with bound.db() as db:
        source = db.scalar(select(SourceFolder))
        assert (source.project_id, source.provider, source.external_id) == (bound.new, bound.adapter.provider, bound.adapter.ids[-1])
        connection = db.get(DriveConnection, bound.connection)
        assert connection.root_folder_id == bound.adapter.ids[-1]
        job = db.get(BackgroundJob, result['job_id'])
        assert job.kind == 'workspace.snapshot'
        assert job.payload['project_id'] == bound.new
        assert db.get(Project, bound.old).name == 'Persistent Project'


def test_confirm_after_completion_does_not_duplicate(bound):
    first = choose(bound).json()
    with bound.db() as db:
        snapshot = db.get(WorkspaceSnapshot, first['id']); snapshot.status = 'ready'; db.commit()
    second = choose(bound).json()
    assert second['id'] == first['id']
    assert second['already_queued'] is True


def _managed_copy(bound, copy_id='managed-copy-id'):
    first = choose(bound).json()
    with bound.db() as db:
        snapshot = db.get(WorkspaceSnapshot, first['id'])
        snapshot.status = 'ready'
        session = OrganizerSession(
            project_id=bound.new,
            source_folder_id=bound.adapter.ids[-1],
            source_folder_name='Project #1',
            copy_folder_id=copy_id,
            copy_folder_name='Project #1 managed copy',
            status='proposed',
            progress=100,
        )
        db.add(session); db.flush()
        snapshot.analysis_status = 'ready'
        snapshot.analysis_result = {
            **dict(snapshot.analysis_result or {}),
            'mode': 'safe_copy',
            'organizer_session_id': session.id,
            'copy_folder_id': copy_id,
            'status': 'proposed',
            'originals_modified': False,
        }
        db.commit()
        return snapshot.id, session.id


@contextmanager
def _cleanup_claim(bound, payload):
    """Exercise the handler with the same exact claim context as the real worker."""
    with bound.db() as db:
        actor = db.scalar(select(ProjectMember.user_id).where(
            ProjectMember.project_id == bound.new, ProjectMember.role == 'owner'))
        key = f"workspace.safe_copy_cleanup:{bound.new}:{actor}:{payload['command_key']}"
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))
        if job is None:
            job = queue.enqueue(db, 'workspace.safe_copy_cleanup', payload, idempotency_key=key, priority=-100)
        if job.status != 'running':
            job.priority = -100
            db.commit()
            claimed = queue.claim(db, 'synthetic-cleanup-worker')
            assert claimed.id == job.id
            job = claimed
        claim = (job.id, job.worker_id, job.attempts, job.locked_at)
    with queue.execution_owner(claim[0], claim[1], attempt=claim[2], locked_at=claim[3]):
        yield
    with bound.db() as db:
        assert queue.succeed(db, claim[0], claim[1])


def _run_cleanup(bound, payload):
    from app.organizer_engine import managed_copies as lifecycle
    with _cleanup_claim(bound, payload):
        return lifecycle.run_managed_copy_cleanup(payload)


def test_managed_copy_cleanup_is_cas_idempotent_and_preserves_history(bound, monkeypatch):
    from app.organizer_engine import managed_copies as lifecycle
    from app.jobs import handlers

    snapshot_id, session_id = _managed_copy(bound)
    # A lookalike and the original exist, but neither has an exact managed record.
    bound.adapter.items['lookalike'] = bound.adapter.folder(
        'lookalike', 'Project #1 (безопасная копия 2026-09-05 UTC)', bound.adapter.root,
    )
    monkeypatch.setattr(lifecycle, 'SessionLocal', bound.db)
    monkeypatch.setattr(lifecycle, 'storage_for_project', lambda project_id, db: bound.adapter)

    summary = bound.client.get(f'/projects/{bound.new}/safe-copies').json()
    assert summary['count'] == 1 and summary['managed_only'] is True
    command_key = f'cleanup:{bound.adapter.provider}:001'
    response = bound.client.post(
        f'/projects/{bound.new}/safe-copies/trash',
        headers={'Idempotency-Key': command_key},
        json={
            'confirmation': 'New project',
            'expected_cleanup_version': summary['cleanup_version'],
            'command_key': command_key,
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    with bound.db() as db:
        queued = db.get(BackgroundJob, job['job_id'])
        assert set(queued.payload) == {'project_id', 'cleanup_version', 'command_key'}
        payload = dict(queued.payload)

    with _cleanup_claim(bound, payload):
        result = handlers.run('workspace.safe_copy_cleanup', payload)
        replay = handlers.run('workspace.safe_copy_cleanup', payload)
    assert result['message'] == 'Копии удалены, можете архивировать проект'
    assert replay['idempotent_replay'] is True
    assert set(replay) == set(result)
    assert replay['originals_affected'] is False
    assert {key: value for key, value in replay.items() if key != 'idempotent_replay'} == {
        key: value for key, value in result.items() if key != 'idempotent_replay'
    }
    assert bound.adapter.trash_calls == ['managed-copy-id']
    assert 'lookalike' not in bound.adapter.trashed
    assert bound.adapter.ids[-1] not in bound.adapter.trashed
    with bound.db() as db:
        # Historical identity is immutable; cleanup is represented by receipts.
        session = db.get(OrganizerSession, session_id)
        assert session.copy_folder_id == 'managed-copy-id'
        snapshot = db.get(WorkspaceSnapshot, snapshot_id)
        assert snapshot.analysis_result['copy_folder_id'] == 'managed-copy-id'
    assert bound.client.get(f'/projects/{bound.new}/safe-copies').json()['count'] == 0
    replay_http = bound.client.post(
        f'/projects/{bound.new}/safe-copies/trash',
        headers={'Idempotency-Key': command_key},
        json={'confirmation': 'New project', 'expected_cleanup_version': summary['cleanup_version'],
              'command_key': command_key},
    )
    assert replay_http.status_code == 200
    assert replay_http.json()['job_id'] == job['job_id']


def test_cleanup_after_earlier_completed_generation(bound, monkeypatch):
    from app.organizer_engine import managed_copies as lifecycle
    snapshot_id, session_id = _managed_copy(bound)
    monkeypatch.setattr(lifecycle, 'SessionLocal', bound.db)
    monkeypatch.setattr(lifecycle, 'storage_for_project', lambda project_id, db: bound.adapter)
    with bound.db() as db:
        version = lifecycle.cleanup_version(lifecycle.managed_copies(db, bound.new))
    _run_cleanup(bound, {'project_id': bound.new, 'cleanup_version': version,
                                        'command_key': 'cleanup:first:001'})
    with bound.db() as db:
        old = db.get(WorkspaceSnapshot, snapshot_id)
        session = OrganizerSession(project_id=bound.new, source_folder_id=bound.adapter.ids[-1],
                                   source_folder_name='Synthetic', copy_folder_id='second-copy',
                                   status='proposed', progress=100)
        db.add(session); db.flush()
        fresh = WorkspaceSnapshot(project_id=bound.new, source_folder_id=old.source_folder_id,
                                  status='ready', analysis_result={**old.analysis_result,
                                      'organizer_session_id': session.id, 'copy_folder_id': 'second-copy'})
        db.add(fresh); db.commit()
        version = lifecycle.cleanup_version(lifecycle.managed_copies(db, bound.new))
    result = _run_cleanup(bound, {'project_id': bound.new, 'cleanup_version': version,
                                                 'command_key': 'cleanup:second:001'})
    assert result['trashed'] == 1
    assert bound.adapter.trash_calls == ['managed-copy-id', 'second-copy']


def test_copy_that_is_another_projects_original_is_excluded(bound):
    _managed_copy(bound)
    with bound.db() as db:
        db.add(SourceFolder(project_id=bound.old, provider=bound.adapter.provider,
                            external_id='managed-copy-id', name='Other original'))
        db.commit()
    assert bound.client.get(f'/projects/{bound.new}/safe-copies').json()['count'] == 0


def test_cleanup_summary_and_status_deny_outsider(bound):
    _managed_copy(bound)
    assert bound.client.get(f'/projects/{bound.new}/safe-copies', headers={'X-User': 'other'}).status_code == 403
    assert bound.client.get(f'/projects/{bound.new}/safe-copies/cleanup/1', headers={'X-User': 'other'}).status_code == 403


def test_cleanup_rejects_stale_version_and_mismatched_idempotency_key(bound):
    _managed_copy(bound)
    summary = bound.client.get(f'/projects/{bound.new}/safe-copies').json()
    body = {
        'confirmation': 'New project',
        'expected_cleanup_version': '0' * 64,
        'command_key': 'cleanup:stale:001',
    }
    stale = bound.client.post(
        f'/projects/{bound.new}/safe-copies/trash',
        headers={'Idempotency-Key': body['command_key']}, json=body,
    )
    assert stale.status_code == 409
    body['expected_cleanup_version'] = summary['cleanup_version']
    mismatch = bound.client.post(
        f'/projects/{bound.new}/safe-copies/trash',
        headers={'Idempotency-Key': 'different-command'}, json=body,
    )
    assert mismatch.status_code == 422


def test_cleanup_recovers_after_crash_without_second_provider_effect(bound, monkeypatch):
    from app.organizer_engine import managed_copies as lifecycle

    _managed_copy(bound)
    monkeypatch.setattr(lifecycle, 'SessionLocal', bound.db)
    monkeypatch.setattr(lifecycle, 'storage_for_project', lambda project_id, db: bound.adapter)
    with bound.db() as db:
        records = lifecycle.managed_copies(db, bound.new)
        version = lifecycle.cleanup_version(records)
    payload = {
        'project_id': bound.new,
        'cleanup_version': version,
        'command_key': 'cleanup:crash:001',
    }
    bound.adapter.crash_after_trash_once = True
    with pytest.raises(RuntimeError):
        _run_cleanup(bound, payload)
    result = _run_cleanup(bound, payload)
    assert result['trashed'] == 1
    # The adapter received a replay, while its provider-effect set stayed singular.
    assert bound.adapter.trash_calls == ['managed-copy-id', 'managed-copy-id']
    assert bound.adapter.trashed == {'managed-copy-id'}


def test_cleanup_lease_recovery_after_item_receipt_finishes_without_provider_replay(bound, monkeypatch):
    from app.organizer_engine import managed_copies as lifecycle

    _managed_copy(bound)
    monkeypatch.setattr(lifecycle, 'SessionLocal', bound.db)
    monkeypatch.setattr(lifecycle, 'storage_for_project', lambda project_id, db: bound.adapter)
    with bound.db() as db:
        records = lifecycle.managed_copies(db, bound.new)
        version = lifecycle.cleanup_version(records)
        db.add(AuditLog(
            action=lifecycle.ITEM_RECEIPT,
            entity_type='project',
            entity_id=bound.new,
            details=json.dumps({
                'schema': lifecycle.SCHEMA,
                'identity': records[0].identity,
                'command_key': 'cleanup:lease:001',
                'provider': bound.adapter.provider,
                'source_revision': records[0].source_revision,
            }, sort_keys=True, separators=(',', ':')),
        ))
        db.commit()
    result = _run_cleanup(bound, {
        'project_id': bound.new,
        'cleanup_version': version,
        'command_key': 'cleanup:lease:001',
    })
    assert result['trashed'] == 1
    assert bound.adapter.trash_calls == []


def test_cleanup_fails_closed_when_provider_has_not_proven_retry_safety(bound, monkeypatch):
    from app.organizer_engine import managed_copies as lifecycle

    _managed_copy(bound)
    monkeypatch.setattr(lifecycle, 'SessionLocal', bound.db)
    monkeypatch.setattr(lifecycle, 'storage_for_project', lambda project_id, db: bound.adapter)
    monkeypatch.setattr(bound.adapter, 'supports_managed_copy_cleanup', False)
    with bound.db() as db:
        records = lifecycle.managed_copies(db, bound.new)
        version = lifecycle.cleanup_version(records)
    with pytest.raises(ValueError, match='managed_copy_cleanup_capability_unavailable'):
        _run_cleanup(bound, {
            'project_id': bound.new,
            'cleanup_version': version,
            'command_key': 'cleanup:capability:001',
        })
    assert bound.adapter.trash_calls == []


def test_archived_project_cannot_enqueue_or_execute_new_managed_copy(bound):
    first = choose(bound).json()
    with bound.db() as db:
        snapshot = db.get(WorkspaceSnapshot, first['id'])
        snapshot.status = 'ready'
        db.get(Project, bound.new).archived_at = workspace.datetime.now(workspace.timezone.utc)
        db.commit()
    before = len(bound.adapter.calls)
    response = bound.client.post(f'/projects/{bound.new}/snapshots/{first["id"]}/standardize')
    assert response.status_code == 409
    assert len(bound.adapter.calls) == before


def test_explicit_refresh_invalidates_completed_snapshot_cache(bound):
    first = choose(bound).json()
    workspace._build_snapshot(first['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    with bound.db() as db:
        db.get(BackgroundJob, first['job_id']).status = 'completed'
        db.commit()

    changed_id = f'{bound.adapter.ids[-1]}/synthetic-contract'
    bound.adapter.items[changed_id] = StorageObject(
        changed_id,
        'Contract.pdf',
        'application/pdf',
        bound.adapter.ids[-1],
        md5_checksum='revision-1',
        size=101,
        modified_time='2026-09-05T08:00:00Z',
        provider=bound.adapter.provider,
    )
    refreshed = choose(bound, params={
        'provider': bound.adapter.provider,
        'connection_id': 'chosen-account',
        'refresh': 'true',
    }).json()
    assert refreshed['id'] != first['id']
    assert refreshed['already_queued'] is False

    # A repeated refresh while the new snapshot is queued is idempotent.
    repeated = choose(bound, params={
        'provider': bound.adapter.provider,
        'connection_id': 'chosen-account',
        'refresh': 'true',
    }).json()
    assert (repeated['id'], repeated['job_id'], repeated['already_queued']) == (
        refreshed['id'], refreshed['job_id'], True,
    )

    workspace._build_snapshot(refreshed['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    with bound.db() as db:
        db.get(BackgroundJob, refreshed['job_id']).status = 'completed'
        db.commit()
        current = db.scalar(select(VirtualNode).where(
            VirtualNode.snapshot_id == refreshed['id'],
            VirtualNode.external_id == changed_id,
        ))
        assert (current.checksum, current.size_bytes) == ('revision-1', 101)
        assert db.scalar(select(VirtualNode).where(
            VirtualNode.snapshot_id == first['id'],
            VirtualNode.external_id == changed_id,
        )) is None

    bound.adapter.items[changed_id].md5_checksum = 'revision-2'
    bound.adapter.items[changed_id].size = 202
    bound.adapter.items[changed_id].modified_time = '2026-09-05T09:00:00Z'
    second_refresh = choose(bound, params={
        'provider': bound.adapter.provider,
        'connection_id': 'chosen-account',
        'refresh': 'true',
    }).json()
    workspace._build_snapshot(second_refresh['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    with bound.db() as db:
        changed = db.scalar(select(VirtualNode).where(
            VirtualNode.snapshot_id == second_refresh['id'],
            VirtualNode.external_id == changed_id,
        ))
        previous = db.scalar(select(VirtualNode).where(
            VirtualNode.snapshot_id == refreshed['id'],
            VirtualNode.external_id == changed_id,
        ))
        assert (changed.checksum, changed.size_bytes) == ('revision-2', 202)
        assert (previous.checksum, previous.size_bytes) == ('revision-1', 101)


def test_snapshot_analysis_reports_measured_progress_and_bound_result(bound):
    file_id = f'{bound.adapter.ids[-1]}/contract.txt'
    bound.adapter.items[file_id] = StorageObject(
        file_id,
        'Contract C-100.txt',
        'text/plain',
        bound.adapter.ids[-1],
        md5_checksum='analysis-input',
        size=56,
        provider=bound.adapter.provider,
    )
    selected = choose(bound).json()
    workspace._build_snapshot(selected['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    with bound.db() as db:
        db.get(BackgroundJob, selected['job_id']).status = 'completed'
        db.commit()

    queued = bound.client.post(f'/projects/{bound.new}/snapshots/{selected["id"]}/analyze')
    assert queued.status_code == 200
    with bound.db() as db:
        job = queue.claim(db, f'{bound.adapter.provider}-worker')
        assert job.kind == 'workspace.analysis'
        payload = dict(job.payload)
        job_id = job.id
    result = handlers.run('workspace.analysis', payload)
    with bound.db() as db:
        assert queue.succeed(db, job_id, f'{bound.adapter.provider}-worker', result)

    status = bound.client.get(f'/projects/{bound.new}/processing-queue').json()
    snapshot = next(item for item in status['snapshots'] if item['id'] == selected['id'])
    assert snapshot['job_status'] == 'completed'
    assert snapshot['job_progress'] == 100
    with bound.db() as db:
        stored = db.get(WorkspaceSnapshot, selected['id'])
        assert stored.analysis_status == 'ready'
        assert stored.analysis_result['storage_binding'] == {
            'project_id': bound.new,
            'provider': bound.adapter.provider,
            'connection_id': 'chosen-account',
            'connection_row_id': bound.connection,
            'folder_id': bound.adapter.ids[-1],
        }
        assert stored.analysis_result['status'] == 'ready'
        assert stored.analysis_result['text_extracted'] == 1
    assert bound.client.get(f'/projects/{bound.old}/snapshots').json()['snapshots'] == []


def test_async_payload_cannot_target_another_project(bound):
    result = choose(bound).json()
    with pytest.raises(HTTPException):
        workspace._build_snapshot(result['id'], bound.old, bound.adapter.ids[-1], raise_errors=True)
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, result['id']).status == 'building'
        assert not list(db.scalars(select(VirtualNode)))


def test_stale_connection_rejected_before_provider_access(bound):
    response = choose(bound, params={'provider': bound.adapter.provider, 'connection_id': 'old-account'})
    assert response.status_code == 409
    assert bound.adapter.calls == []


def test_provider_locator_is_not_interchangeable(bound):
    invalid = 'opaque-google-id' if bound.adapter.provider == 'yandex_disk' else 'disk:/Customer'
    response = choose(bound, invalid)
    assert response.status_code == 422
    assert bound.adapter.calls == []


def test_browse_restores_selected_folder_and_project(bound):
    queued = choose(bound).json()
    response = bound.client.get(f'/projects/{bound.new}/source-folders/discover')
    assert response.status_code == 200
    data = response.json()
    assert data['folder_id'] == bound.adapter.ids[-1]
    assert data['project_id'] == bound.new
    assert [row['id'] for row in data['breadcrumbs']][-3:] == bound.adapter.ids
    parent_view = bound.client.get(
        f'/projects/{bound.new}/source-folders/discover',
        params={'folder_id': bound.adapter.ids[-2]},
    ).json()
    selected = next(row for row in parent_view['folders'] if row['id'] == bound.adapter.ids[-1])
    assert selected['job_id'] == queued['job_id']
    assert selected['job_status'] == 'queued'
    assert selected['job_progress'] == 0


def test_discovery_reloads_provider_children_without_stale_cache(bound):
    parent = bound.adapter.ids[-1]
    first = bound.client.get(
        f'/projects/{bound.new}/source-folders/discover',
        params={'folder_id': parent, 'provider': bound.adapter.provider, 'connection_id': 'chosen-account'},
    ).json()
    assert first['folders'] == []

    added_id = f'{parent}/new-child'
    bound.adapter.items[added_id] = bound.adapter.folder(added_id, 'New child', parent)
    second = bound.client.get(
        f'/projects/{bound.new}/source-folders/discover',
        params={'folder_id': parent, 'provider': bound.adapter.provider, 'connection_id': 'chosen-account'},
    ).json()
    assert [(item['id'], item['name']) for item in second['folders']] == [(added_id, 'New child')]
    assert second['project_id'] == bound.new
    assert second['provider'] == bound.adapter.provider
    assert second['connection_id'] == 'chosen-account'


def test_yandex_app_namespace_parent_is_preserved():
    item = YandexDiskStorageAdapter._to_object({'path': 'app:/A/B/C', 'name': 'C', 'type': 'dir'})
    assert item.parent_id == 'app:/A/B'


def test_storage_factory_rejects_credential_from_another_connection(db_session, monkeypatch):
    org = Organization(name='Synthetic'); db_session.add(org); db_session.flush()
    p = Project(name='Project', organization_id=org.id); db_session.add(p); db_session.flush()
    cred = IntegrationCredential(project_id=p.id, provider='yandex_disk', capability='storage', access_token='fake')
    db_session.add(cred); db_session.flush()
    db_session.add(DriveConnection(project_id=p.id, provider='yandex_disk', connection_id='different-id',
                                   account_email='test@example.test', root_folder_id='disk:/'))
    db_session.commit()
    monkeypatch.setattr(storage, 'decrypt_token', lambda _: 'synthetic')
    with pytest.raises(HTTPException) as failure:
        storage.storage_for_project(p.id, db_session)
    assert failure.value.status_code == 409


def test_reconfirm_registered_folder_restores_selected_root(bound):
    first = choose(bound).json()
    choose(bound, bound.adapter.other)
    assert choose(bound).json()['id'] == first['id']
    with bound.db() as db:
        assert db.get(DriveConnection, bound.connection).root_folder_id == bound.adapter.ids[-1]


def test_ready_snapshot_waits_for_explicit_safe_copy_request(bound):
    result = choose(bound).json()
    workspace._build_snapshot(result['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    with bound.db() as db:
        db.get(BackgroundJob, result['job_id']).status = 'completed'
        db.commit()
        assert db.scalar(select(BackgroundJob).where(BackgroundJob.kind == 'workspace.safe_copy')) is None
        assert len(list(db.scalars(select(VirtualNode)))) == 1
    explicit = bound.client.post(f'/projects/{bound.new}/snapshots/{result["id"]}/standardize')
    assert explicit.status_code == 200
    with bound.db() as db:
        assert db.scalar(select(BackgroundJob).where(BackgroundJob.kind == 'workspace.safe_copy')) is not None


def test_safe_copy_enqueue_failure_does_not_commit_orphan_session(bound, monkeypatch):
    result = choose(bound).json()
    def crash(*args, **kwargs):
        raise RuntimeError('synthetic queue unavailable')
    monkeypatch.setattr(queue, 'enqueue', crash)
    with pytest.raises(RuntimeError):
        workspace._start_safe_copy_pipeline(result['id'], bound.new, bound.adapter.ids[-1], 'Project #1')
    with bound.db() as db:
        assert not list(db.scalars(select(OrganizerSession)))


def test_explicit_analysis_preserves_storage_binding(bound):
    result = choose(bound).json()
    with bound.db() as db:
        # The previous snapshot job has finished before a new manual analysis.
        db.get(BackgroundJob, result['job_id']).status = 'completed'
        snap = db.get(WorkspaceSnapshot, result['id']); snap.status = 'ready'; db.commit()
        binding = snap.analysis_result['storage_binding']
    response = bound.client.post(f'/projects/{bound.new}/snapshots/{result["id"]}/analyze')
    assert response.status_code == 200
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, result['id']).analysis_result['storage_binding'] == binding


def test_navigation_three_levels_back_and_duplicate_names(bound):
    for identifier in [bound.adapter.root, *bound.adapter.ids, bound.adapter.ids[1]]:
        response = bound.client.get(f'/projects/{bound.new}/source-folders/discover', params={'folder_id': identifier})
        assert response.status_code == 200
        data = response.json()
        assert data['folder_id'] == identifier
        assert data['breadcrumbs'][-1]['id'] == identifier
    first = choose(bound).json()
    other = choose(bound, bound.adapter.other).json()
    assert first['source_folder'] == other['source_folder']
    assert first['id'] != other['id']
    assert choose(bound, bound.adapter.ids[0]).status_code == 200


def test_http_repeat_does_not_duplicate_queued_job(bound):
    first = choose(bound).json()
    assert choose(bound).json()['job_id'] == first['job_id']
    with bound.db() as db:
        assert len(list(db.scalars(select(BackgroundJob)))) == 1


def test_access_and_provider_mismatch_are_denied(bound):
    assert choose(bound, headers={'X-User': 'other'}).status_code == 403
    assert bound.client.get(f'/projects/{bound.new}/source-folders/discover', headers={'X-User': 'other'}).status_code == 403
    wrong = 'google_drive' if bound.adapter.provider == 'yandex_disk' else 'yandex_disk'
    assert choose(bound, params={'provider': wrong}).status_code == 409
    assert bound.adapter.calls == []
    with bound.db() as db:
        db.get(DriveConnection, bound.connection).status = 'disconnected'; db.commit()
    assert choose(bound).status_code == 409


def test_connection_changed_before_worker_is_safe_failure(bound):
    result = choose(bound).json()
    bound.adapter.calls.clear()
    with bound.db() as db:
        db.get(DriveConnection, bound.connection).connection_id = 'replacement'; db.commit()
    with pytest.raises(HTTPException):
        workspace._build_snapshot(result['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    assert bound.adapter.calls == []
    assert bound.client.post(f'/projects/{bound.new}/snapshots/{result["id"]}/retry-build').status_code == 409


def test_safe_error_and_explicit_retry_keep_target(bound):
    result = choose(bound).json()
    bound.adapter.error = RuntimeError('synthetic secret=do-not-publish document-body')
    with pytest.raises(RuntimeError):
        workspace._build_snapshot(result['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    with bound.db() as db:
        snap = db.get(WorkspaceSnapshot, result['id'])
        assert snap.status == 'failed'
        assert 'do-not-publish' not in snap.error_message
        # Explicit retry is permitted after terminal queue failure, not while
        # an automatic retry for this snapshot is still pending.
        db.get(BackgroundJob, result['job_id']).status = 'failed'
        db.commit()
    assert choose(bound).json()['id'] == result['id']
    assert bound.client.post(f'/projects/{bound.old}/snapshots/{result["id"]}/retry-build').status_code == 404
    assert bound.client.post(f'/projects/{bound.new}/snapshots/{result["id"]}/retry-build', headers={'X-User': 'other'}).status_code == 403
    bound.adapter.error = None
    retry = bound.client.post(f'/projects/{bound.new}/snapshots/{result["id"]}/retry-build')
    assert retry.status_code == 200
    with bound.db() as db:
        retry_job = db.scalar(select(BackgroundJob).order_by(BackgroundJob.id.desc()))
        assert retry_job.payload['project_id'] == bound.new
        assert retry_job.payload['external_id'] == bound.adapter.ids[-1]


def test_real_job_dispatch_and_explicit_safe_copy_keep_new_project(bound, monkeypatch):
    import app.organizer as organizer
    result = choose(bound).json()
    with bound.db() as db:
        job = queue.claim(db, 'synthetic-worker')
        assert job.id == result['job_id'] and job.progress == 1
        payload = dict(job.payload)
    dispatched = handlers.run('workspace.snapshot', payload)
    assert dispatched == {'snapshot_id': result['id']}
    with bound.db() as db:
        assert queue.succeed(db, result['job_id'], 'synthetic-worker', dispatched)
        assert db.get(BackgroundJob, result['job_id']).progress == 100
        assert db.scalar(select(BackgroundJob).where(BackgroundJob.kind == 'workspace.safe_copy')) is None
    start = bound.client.post(f'/projects/{bound.new}/snapshots/{result["id"]}/standardize')
    assert start.status_code == 200
    with bound.db() as db:
        followup = db.scalar(select(BackgroundJob).where(BackgroundJob.kind == 'workspace.safe_copy'))
        assert followup.payload['project_id'] == bound.new
        next_payload = dict(followup.payload)
    def fake_scan(session_id, project_id, source_folder_id, **kwargs):
        assert project_id == bound.new and source_folder_id == bound.adapter.ids[-1]
        with bound.db() as db:
            row = db.get(OrganizerSession, session_id)
            row.status = 'proposed'; row.progress = 100
            row.copy_folder_id = 'synthetic-copy'; row.copy_folder_name = 'Synthetic copy'
            db.commit()
    monkeypatch.setattr(organizer, '_scan_worker', fake_scan)
    handlers.run('workspace.safe_copy', next_payload)
    snapshots = bound.client.get(f'/projects/{bound.new}/snapshots').json()['snapshots']
    assert snapshots[0]['analysis_status'] == 'ready'
    assert snapshots[0]['storage_binding']['project_id'] == bound.new
    assert bound.client.get(f'/projects/{bound.old}/snapshots').json()['snapshots'] == []
    assert bound.client.get(f'/projects/{bound.new}').json()['name'] == 'New project'
    progress = bound.client.get(f'/projects/{bound.new}/processing-queue').json()
    assert progress['sessions'][0]['progress'] == 100


def test_atomic_confirmation_rolls_back_when_enqueue_fails(bound, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError('synthetic enqueue failure')
    monkeypatch.setattr(queue, 'enqueue', crash)
    with pytest.raises(RuntimeError):
        choose(bound)
    with bound.db() as db:
        assert not list(db.scalars(select(SourceFolder)))
        assert not list(db.scalars(select(WorkspaceSnapshot)))
        assert db.get(DriveConnection, bound.connection).root_folder_id == bound.adapter.root


def test_google_oauth_only_project_can_confirm_its_own_folder(bound):
    if bound.adapter.provider != 'google_drive':
        return
    with bound.db() as db:
        db.delete(db.get(DriveConnection, bound.connection))
        db.add(GoogleOAuthToken(project_id=bound.new, access_token='synthetic-encrypted'))
        db.commit()
    browse = bound.client.get(f'/projects/{bound.new}/source-folders/discover', params={'folder_id': 'root'})
    assert browse.status_code == 200
    result = choose(bound)
    assert result.status_code == 200
    with bound.db() as db:
        connection = db.scalar(select(DriveConnection).where(DriveConnection.project_id == bound.new))
        assert connection.root_folder_id == bound.adapter.ids[-1]
        assert connection.connection_id.startswith('google-token:')


def test_old_project_credentials_never_authorize_new_project(bound):
    with bound.db() as db:
        db.delete(db.get(DriveConnection, bound.connection))
        db.add(GoogleOAuthToken(project_id=bound.old, access_token='synthetic-old'))
        db.commit()
    assert choose(bound).status_code == 409
    assert bound.adapter.calls == []


def test_safe_copy_rejects_session_from_old_project(bound):
    result = choose(bound).json()
    with bound.db() as db:
        old_session = OrganizerSession(project_id=bound.old, source_folder_id=bound.adapter.ids[-1], source_folder_name='Old')
        db.add(old_session); db.commit(); session_id = old_session.id
    with pytest.raises(HTTPException) as error:
        workspace._run_safe_copy_pipeline(result['id'], session_id, bound.new, bound.adapter.ids[-1], raise_errors=True)
    assert error.value.status_code == 409


@pytest.mark.parametrize('locator', ['app:/A/B/C', 'disk:/A/B/C'])
def test_yandex_namespace_breadcrumb_without_network(locator):
    class Adapter:
        provider = 'yandex_disk'
        def get_object(self, path):
            return YandexDiskStorageAdapter._to_object({'path': path, 'name': path.rsplit('/', 1)[-1], 'type': 'dir'})
    assert [item['id'] for item in workspace._storage_breadcrumb(Adapter(), locator)] == [
        locator.split('/')[0] + '/', locator.rsplit('/', 2)[0], locator.rsplit('/', 1)[0], locator,
    ]
