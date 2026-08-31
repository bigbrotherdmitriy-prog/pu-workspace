import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register all mapped tables
from app.database import Base
from app.models.user import User


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


@pytest.fixture
def user_factory(db_session):
    def create(**overrides):
        sequence = db_session.query(User).count() + 1
        values = {
            "name": f"Тестовый пользователь {sequence}",
            "email": f"user-{sequence}@example.test",
            "is_admin": False,
        }
        values.update(overrides)
        user = User(**values)
        db_session.add(user)
        db_session.flush()
        return user
    return create
