import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture(scope="session")
def postgres_url() -> str:
    if os.getenv("PU_TEST_POSTGRES") != "1":
        pytest.skip("PostgreSQL integration tests require PU_TEST_POSTGRES=1")
    value = os.environ["DATABASE_URL"]
    url = make_url(value.replace("postgresql://", "postgresql+psycopg://", 1))
    if not (url.database or "").endswith("_test"):
        pytest.fail("Refusing to run integration fixtures against a non-test database")
    return value


@pytest.fixture()
def pg_session(postgres_url: str) -> Iterator[Session]:
    engine = create_engine(postgres_url.replace("postgresql://", "postgresql+psycopg://", 1))
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture()
def project_factory(pg_session: Session):
    def create(*, role: str = "owner") -> tuple[User, Project]:
        suffix = uuid4().hex
        organization = Organization(name=f"Test organization {suffix}")
        user = User(name="CI User", email=f"ci-{suffix}@example.test", password_hash="test", is_admin=False)
        pg_session.add_all([organization, user])
        pg_session.flush()
        project = Project(name=f"CI Project {suffix}", organization_id=organization.id)
        pg_session.add(project)
        pg_session.flush()
        pg_session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        pg_session.flush()
        return user, project
    return create
