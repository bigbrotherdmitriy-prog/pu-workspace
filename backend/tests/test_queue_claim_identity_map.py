"""ORM refresh contract; SQL claim concurrency still requires real PostgreSQL."""
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from app.jobs.queue import claim, utcnow
from app.models.job import BackgroundJob


def test_postgres_crash_harness_passes_the_complete_execution_fence():
    """Execute the actual harness context call without requiring PostgreSQL."""
    import ast
    from pathlib import Path
    from app.jobs.queue import current_execution_claim, execution_owner

    tree = ast.parse(Path(__file__).with_name(
        "test_mvp1_storage_mutation_postgres_runtime.py").read_text(encoding="utf8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "execution_owner"]
    assert len(calls) == 1
    owner = (1, "synthetic-worker", 2, utcnow())
    expression = ast.Expression(body=calls[0])
    context = eval(compile(expression, "harness-execution-fence", "eval"),
                   {"execution_owner": execution_owner, "owner": owner})
    with context:
        assert current_execution_claim() == owner
    assert current_execution_claim() is None


def test_postgres_claim_refreshes_an_already_loaded_job():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    BackgroundJob.__table__.create(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            row = BackgroundJob(kind="synthetic.claim", payload={}, status="retrying",
                                attempts=1, worker_id="old-worker", progress=0)
            session.add(row)
            session.commit()
            job_id = row.id
            assert session.get(BackgroundJob, job_id) is row

            class PostgresClaimBoundary:
                # Keep the real identity map/commit/get. Only the PostgreSQL SQL
                # transport is substituted with an equivalent local update.
                bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

                def execute(self, statement, values):
                    assert "FOR UPDATE SKIP LOCKED" in str(statement)
                    return session.execute(update(BackgroundJob).where(BackgroundJob.id == job_id)
                        .values(status="running", worker_id=values["worker_id"], attempts=2,
                                locked_at=utcnow(), lease_expires_at=utcnow() + timedelta(seconds=60),
                                progress=1).returning(BackgroundJob.id)
                        .execution_options(synchronize_session=False))

                def commit(self):
                    session.commit()

                def get(self, *args, **kwargs):
                    return session.get(*args, **kwargs)

            claimed = claim(PostgresClaimBoundary(), "new-worker", lease_seconds=60)
            assert claimed is row
            assert (claimed.status, claimed.worker_id, claimed.attempts, claimed.progress) == (
                "running", "new-worker", 2, 1,
            )
            assert claimed.locked_at is not None and claimed.lease_expires_at is not None
    finally:
        engine.dispose()
