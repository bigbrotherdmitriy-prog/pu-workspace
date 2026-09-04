from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_deploy_restores_backup_in_an_isolated_database_before_switch():
    deploy = (ROOT / "scripts/deploy-production.sh").read_text(encoding="utf-8")

    backup_step = deploy.index('[2/6] creating and validating PostgreSQL backup')
    switch_step = deploy.index('[3/6] preserving rollback image')
    restore_step = deploy.index('pg_restore --no-owner -d restore_check')
    assert backup_step < restore_step < switch_step
    assert 'initdb -D /tmp/restore-db' in deploy
    assert 'table_count=$(psql -d restore_check' in deploy
    assert '[ "$table_count" -gt 0 ]' in deploy


def test_operations_runbook_uses_portable_non_production_restore():
    operations = (ROOT / "docs/operations.md").read_text(encoding="utf-8")

    assert "pg_restore --no-owner" in operations
    assert "Не проверяйте восстановление поверх production-БД" in operations
