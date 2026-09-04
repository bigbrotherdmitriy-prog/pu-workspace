from sqlalchemy import text

from app.schema import CURRENT_SCHEMA_REVISION


def test_postgres_has_latest_schema_and_transactional_factories(pg_session, project_factory):
    assert pg_session.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
    user, project = project_factory()
    assert user.id and project.id
    assert pg_session.scalar(text("SELECT count(*) FROM project_members WHERE project_id=:id"), {"id": project.id}) == 1
