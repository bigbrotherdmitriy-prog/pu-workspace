from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_production_deploy_is_fail_closed_and_uses_persistent_proxy_override():
    source = (ROOT / "scripts" / "deploy-production.sh").read_text(encoding="utf-8")
    assert "set -eu" in source
    assert "/opt/pu-workspace/docker-compose.proxy.yml" in source
    assert "pg_restore -l" in source
    assert "python -m pytest tests -q" in source
    assert "api/readiness" in source
    assert "rollback()" in source
    assert "restoring previous release" in source


def test_ci_runs_backend_tests_and_frontend_build():
    source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest tests -q" in source
    assert "pnpm install --frozen-lockfile" in source
    assert "pnpm run build" in source
