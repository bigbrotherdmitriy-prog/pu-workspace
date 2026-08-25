import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.auth import cleanup_expired_sessions, require_project_role
from app.database import Base
import app.models  # noqa: F401
from app.models.auth_session import AuthSession
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


class RbacTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.owner = User(name="Owner", email="owner@test", is_admin=False)
        self.outsider = User(name="Outsider", email="out@test", is_admin=False)
        self.project = Project(name="Private")
        self.db.add_all([self.owner, self.outsider, self.project]); self.db.flush()
        self.db.add(ProjectMember(project_id=self.project.id, user_id=self.owner.id, role="owner")); self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def test_outsider_cannot_read_project(self):
        with self.assertRaises(HTTPException) as caught:
            require_project_role(self.db, self.outsider, self.project.id, "viewer")
        self.assertEqual(caught.exception.status_code, 403)

    def test_owner_satisfies_manager_requirement(self):
        self.assertEqual(require_project_role(self.db, self.owner, self.project.id, "manager"), "owner")

    def test_expired_sessions_are_removed_only(self):
        now = datetime.now(timezone.utc)
        self.db.add_all([
            AuthSession(user_id=self.owner.id, token_hash="a" * 64, expires_at=now - timedelta(seconds=1)),
            AuthSession(user_id=self.owner.id, token_hash="b" * 64, expires_at=now + timedelta(hours=1)),
        ]); self.db.commit()
        self.assertEqual(cleanup_expired_sessions(self.db), 1)
        self.assertEqual(self.db.query(AuthSession).count(), 1)


if __name__ == "__main__":
    unittest.main()
