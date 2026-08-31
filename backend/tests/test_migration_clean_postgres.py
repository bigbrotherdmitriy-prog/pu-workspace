from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_proposal_timestamp_migration_is_safe_after_squashed_initial_schema():
    initial = (ROOT / "migrations/versions/4d7fb326d458_initial_schema.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/7b0f3e3d9a12_add_proposal_completion_timestamps.py").read_text(encoding="utf-8")

    assert "sa.Column('applied_at'" in initial
    assert "sa.Column('rolled_back_at'" in initial
    assert "ADD COLUMN IF NOT EXISTS applied_at" in migration
    assert "ADD COLUMN IF NOT EXISTS rolled_back_at" in migration
