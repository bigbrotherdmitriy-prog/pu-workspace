"""Codex fallback: real model FK behavior with/without redundant constraints."""
from datetime import timedelta
import pytest
from sqlalchemy import ForeignKeyConstraint, MetaData, create_engine, event, insert, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import app.models
from app.database import Base
from v54_pilot_fixture import seed, NOW, uid


@pytest.fixture(params=[False, True], ids=['current-orm', 'migration-composites'])
def world(request):
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    if not request.param:
        for name, column, target in [
            ('v54_mailbox_project_cohorts', 'organization_id', 'organizations.id'),
            ('v54_materializations', 'project_id', 'projects.id')]:
            table = metadata.tables[name]
            assert not any(list(f.column_keys) == [column] for f in table.foreign_key_constraints)
            table.append_constraint(ForeignKeyConstraint([column], [target], ondelete='RESTRICT'))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def enable_fk(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    with Session(engine) as db:
        seed(db)
        db.commit()
    with engine.connect() as conn:
        transaction = conn.begin()
        yield conn, metadata
        transaction.rollback()
    engine.dispose()


def values(kind):
    if kind == 'v54_mailbox_project_cohorts':
        return dict(organization_id=1, project_id=4, mail_connection_id=uid(11),
                    credential_generation=1, enabled=False, record_version=1)
    return dict(id=uid(950), organization_id=1, project_id=4, owner_id=2,
                source_id=uid(13), source_version_id=uid(15), evidence_id=uid(16),
                object_id='a'*32, state='ADMITTED', record_version=1,
                kek_reference='synthetic/key', kek_version='v1', residency='synthetic',
                retention_until=NOW+timedelta(minutes=10), admitted_at=NOW,
                copy_allowed=False, derive_allowed=False)


@pytest.mark.parametrize('kind', ['v54_mailbox_project_cohorts','v54_materializations'])
@pytest.mark.parametrize('change', [dict(project_id=9), dict(project_id=999),
                                   dict(organization_id=2),dict(organization_id=999),
                                   dict(project_id=None), dict(organization_id=None)])
def test_invalid_insert_and_update(world, kind, change):
    conn, metadata = world
    table = metadata.tables[kind]
    valid = values(kind)
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            conn.execute(insert(table).values(**(valid | change)))
    conn.execute(insert(table).values(**valid))
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            conn.execute(update(table).values(**change))


@pytest.mark.parametrize('kind', ['v54_mailbox_project_cohorts','v54_materializations'])
@pytest.mark.parametrize('parent,change', [('projects',None),('organizations',None),
                                         ('projects',dict(organization_id=2)),
                                         ('projects',dict(id=99))])
def test_parent_delete_and_key_mutation_restricted(world, kind, parent, change):
    conn, metadata = world
    conn.execute(insert(metadata.tables[kind]).values(**values(kind)))
    table = metadata.tables[parent]
    condition = table.c.id == (4 if parent == 'projects' else 1)
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            conn.execute(delete(table).where(condition) if change is None else
                         update(table).where(condition).values(**change))
