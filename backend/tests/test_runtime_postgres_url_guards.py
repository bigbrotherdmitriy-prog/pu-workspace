"""No connections: reject unsafe authority runtime database targets."""
import pytest

from test_v54_authority_postgres import safe_url


ENV = "PUW_V54_AUTHORITY_DATABASE_URL"


def test_authority_accepts_owned_github_postgres_service(monkeypatch):
    value = "postgresql://test:test@postgres/puw_v54_test_authority"
    monkeypatch.setenv(ENV, value)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert safe_url(ENV) == value


@pytest.mark.parametrize("host,database,query,ci", [
    ("postgres", "puw_v54_test_authority", "", "false"),
    ("postgres", "puw_v54_test_authority", "", "TRUE"),
    ("production.example.test", "puw_v54_test_authority", "", "true"),
    ("postgres", "production", "", "true"),
    ("postgres", "puw_v54_test_authority", "?host=production.example.test", "true"),
])
def test_authority_rejects_unsafe_database_targets(monkeypatch, host, database, query, ci):
    monkeypatch.setenv(ENV, f"postgresql://test:test@{host}/{database}{query}")
    monkeypatch.setenv("GITHUB_ACTIONS", ci)
    with pytest.raises(AssertionError):
        safe_url(ENV)
