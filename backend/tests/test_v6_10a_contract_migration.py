from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_commercial_upgrade_is_additive_without_inferred_business_data(monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    output = StringIO()
    config = Config(str(backend / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/pu_workspace_test")
    command.upgrade(config, "d021a6c0b002:d021a6c0b003", sql=True)
    sql = output.getvalue()
    assert "vat_mode VARCHAR(20) DEFAULT 'unspecified' NOT NULL" in sql
    for field in ("vat_rate", "performed_from", "performed_to"):
        assert f"ADD COLUMN {field}" in sql
    for table in ("budget_lines", "cash_flow_entries", "acceptance_acts", "payment_events",
                  "contract_budget_proposals", "invoice_extraction_proposals"):
        assert f"ALTER TABLE {table} ADD COLUMN vat_snapshot JSON" in sql
    assert "ck_contract_vat_state" in sql
    assert "ck_contract_performed_order" in sql
    assert "UPDATE contracts" not in sql
    assert "UPDATE budget_lines" not in sql
