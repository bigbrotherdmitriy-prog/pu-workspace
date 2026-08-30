import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analytics"))

from campaign_report import campaign_clicks, source_from_uri


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
