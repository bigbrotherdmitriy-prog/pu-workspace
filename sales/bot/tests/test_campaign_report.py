import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analytics"))

from campaign_report import (
    campaign_clicks,
    funnel_rows,
    lead_counts,
    render_report,
    source_from_uri,
)


class CampaignReportTest(unittest.TestCase):
    def test_extracts_only_safe_campaign_source(self) -> None:
        self.assertEqual(source_from_uri("/go.html?source=ad_finance_chain"), "ad_finance_chain")
        self.assertIsNone(source_from_uri("/go.html?source=bad%20value"))
        self.assertIsNone(source_from_uri("/index.html?source=ad_finance_chain"))

    def test_counts_valid_caddy_json_records(self) -> None:
        lines = [
            json.dumps({"request": {"uri": "/go.html?source=ad_finance_chain"}}),
            json.dumps({"request": {"uri": "/go.html?source=ad_finance_chain"}}),
            json.dumps({"request": {"uri": "/go.html?source=ad_email_control"}}),
            "not-json",
        ]
        self.assertEqual(
            campaign_clicks(lines),
            {"ad_finance_chain": 2, "ad_email_control": 1},
        )

    def test_combines_clicks_and_aggregate_lead_statuses_without_pii(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "sales.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE leads(source TEXT NOT NULL, status TEXT NOT NULL, contact TEXT NOT NULL);
                INSERT INTO leads VALUES ('ad_finance_chain', 'new', 'secret@example.com');
                INSERT INTO leads VALUES ('ad_finance_chain', 'pilot', '+79990000000');
                INSERT INTO leads VALUES ('direct', 'closed', '@client');
                """
            )
            connection.close()
            rows = funnel_rows(
                {"ad_finance_chain": 4, "ad_email_control": 3},
                lead_counts(database),
            )
            report = render_report(rows)
            finance = next(row for row in rows if row.source == "ad_finance_chain")
            self.assertEqual(finance.leads, 2)
            self.assertEqual(finance.statuses["pilot"], 1)
            self.assertEqual(finance.conversion, "50.0%")
            self.assertIn("ad_email_control", report)
            self.assertIn("direct", report)
            self.assertNotIn("secret@example.com", report)
            self.assertNotIn("+79990000000", report)
