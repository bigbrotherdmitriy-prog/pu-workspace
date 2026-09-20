"""Opt-in real PostgreSQL gate for immutable contract version CAS."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import os
from threading import Event, local
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import organizations_contracts as api
from app.database import Base
from app.models.organization_contract import Contract, ContractVersion, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture
def contract_pg_engine():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 contract-version gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    base = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "mvp3_contracts_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_postgres_two_contract_updates_have_one_cas_winner_and_one_version(contract_pg_engine, monkeypatch):
    with Session(contract_pg_engine) as db:
        organization = Organization(name="Contract CAS tenant")
        user = User(name="Owner", email=f"contract-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name="Contract project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner")); db.commit()
        created = api.create_contract(project.id, api.ContractCreate(
            number="CAS-1", title="Initial", status="draft", contract_kind="prime_reference",
        ), db, user)
        contract_id, project_id, user_id = created["id"], project.id, user.id

    locked, release, second_started = Event(), Event(), Event()
    thread_state = local()
    original_ensure_baseline = api._ensure_contract_baseline

    def ensure_baseline_with_barrier(db, row, *, actor_user_id):
        result = original_ensure_baseline(db, row, actor_user_id=actor_user_id)
        if getattr(thread_state, "first", False) and row.record_version == 1:
            locked.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(api, "_ensure_contract_baseline", ensure_baseline_with_barrier)

    def update(first: bool):
        thread_state.first = first
        try:
            with Session(contract_pg_engine) as db:
                if not first:
                    second_started.set()
                actor = db.get(User, user_id)
                return api.update_contract_links(
                    project_id, contract_id,
                    api.ContractLinkUpdate(expected_record_version=1, title="Winner" if first else "Loser"),
                    db, actor,
                )["record_version"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update, True)
        assert locked.wait(10)
        second = pool.submit(update, False)
        assert second_started.wait(5)
        with pytest.raises(TimeoutError):
            second.result(timeout=0.2)
        release.set()
        assert sorted([first.result(timeout=10), second.result(timeout=10)]) == [2, 409]

    with Session(contract_pg_engine) as db:
        assert db.get(Contract, contract_id).record_version == 2
        assert db.scalar(select(func.count()).select_from(ContractVersion).where(
            ContractVersion.contract_id == contract_id,
        )) == 2
