import base64
from pathlib import Path

import pytest

from app.staging import ci_local_upload


def test_ci_runtime_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PU_LOCAL_UPLOAD_CI_RUNTIME", raising=False)
    assert ci_local_upload.install_ci_local_upload_runtime() is False


@pytest.mark.parametrize("database_url", [
    "postgresql://pu_user:secret@db:5432/pu_workspace",
    "postgresql://pu_test:secret@production-db:5432/pu_test",
    "sqlite:///pu_test.db",
])
def test_ci_runtime_rejects_non_ci_database(monkeypatch, database_url):
    monkeypatch.setenv("PU_LOCAL_UPLOAD_CI_RUNTIME", "true")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PU_LOCAL_UPLOAD_STAGING_ROOT", str(Path("/var/lib/pu-workspace-ci-staging")))
    with pytest.raises(
        ci_local_upload.CiLocalUploadConfigurationError,
        match="unsafe_ci_local_upload_configuration",
    ):
        ci_local_upload.install_ci_local_upload_runtime()


def test_ci_key_requires_exact_256_bits(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(
        ci_local_upload.CiLocalUploadConfigurationError,
        match="invalid_ci_staging_key",
    ):
        ci_local_upload._key()


def test_ci_compose_explicitly_uses_private_shared_staging_volume():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.ci.yml").read_text(
        encoding="utf-8",
    )
    assert "PU_LOCAL_UPLOAD_CI_RUNTIME: ${PU_LOCAL_UPLOAD_CI_RUNTIME:-false}" in compose
    assert "PU_LOCAL_UPLOAD_STAGING_ROOT: ${PU_LOCAL_UPLOAD_STAGING_ROOT:-}" in compose
    assert "staging:/var/lib/pu-workspace-ci-staging" in compose

    generator = (Path(__file__).resolve().parents[2] / "scripts" / "prepare_test_environment.py").read_text(
        encoding="utf-8",
    )
    assert "'PU_LOCAL_UPLOAD_CI_RUNTIME': 'true'" in generator
    assert "'PU_LOCAL_UPLOAD_STAGING_ROOT': '/var/lib/pu-workspace-ci-staging'" in generator
