from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "v54_mailbox_inventory.py"
SPEC = importlib.util.spec_from_file_location("v54_mailbox_inventory", SCRIPT)
inventory = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(inventory)


class InventoryTests(unittest.TestCase):
    def fixture(self):
        return inventory.load_fixture("mailbox_cutover.json")

    def run_main(self, argv, env=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=True), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = inventory.main(argv)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_fixture_report_is_deterministic_and_expected(self):
        snapshot = self.fixture()
        key = b"k" * 32
        first = inventory.make_report(snapshot, key)
        second = inventory.make_report(snapshot, key)
        self.assertEqual(first, second)
        aggregates = first["aggregates"]
        self.assertEqual(aggregates["gmail_messages_total"], 6)
        self.assertEqual(aggregates["origin_unknown_or_partial"], 2)
        self.assertEqual(aggregates["messages_without_mailbox_provenance"], 2)
        self.assertEqual(aggregates["origin_all_fields_missing"], 2)
        self.assertEqual(aggregates["origin_partial_invalid_or_transitional"], 0)
        self.assertEqual(aggregates["legacy_source_key_collision_groups"], 1)
        self.assertEqual(aggregates["provider_id_across_source_type_groups"], 1)
        self.assertEqual(aggregates["mailbox_scoped_key_collision_groups"], 1)
        self.assertEqual(aggregates["thread_cross_mailbox_groups"], 1)
        self.assertEqual(aggregates["thread_with_unknown_mailbox_groups"], 1)
        self.assertEqual(aggregates["project_move_proven_by_context_history"], 1)
        self.assertEqual(aggregates["confirmed_context_without_recorded_v54_approver"], 2)
        self.assertEqual(aggregates["confirmed_context_without_trustworthy_human_proof"], 4)
        self.assertEqual(aggregates["drafts_project_mismatch"], 1)
        self.assertEqual(aggregates["explicit_contact_link_rows_if_provided"], 2)
        self.assertEqual(aggregates["contact_identity_multiproject_groups_if_explicit_key_exists"], 1)

    def test_report_contains_only_opaque_samples(self):
        snapshot = self.fixture()
        text = inventory.canonical_json(inventory.make_report(snapshot, b"x" * 32))
        for forbidden in (
            "gmail-shared", "gmail-moved", "thread-shared", "mail-a", "mail-b",
            "project-a", "project-b", "contact-synthetic-001", "actor-001",
            "source-001", "m-001", "task-001", "draft-001",
        ):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("@", text)
        self.assertRegex(text, r"msg_[0-9a-f]{20}")

    def test_opaque_key_changes_ids_not_aggregates(self):
        one = inventory.make_report(self.fixture(), b"a" * 32)
        two = inventory.make_report(self.fixture(), b"b" * 32)
        self.assertEqual(one["aggregates"], two["aggregates"])
        self.assertNotEqual(one["report_id"], two["report_id"])

    def test_offline_fixture_cli_is_deterministic(self):
        args = ["--fixture", "mailbox_cutover.json"]
        first = self.run_main(args)
        second = self.run_main(args)
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0)
        payload = json.loads(first[1])
        self.assertEqual(payload["mode"], "offline_fixture")
        self.assertEqual(payload["report"]["aggregates"]["gmail_messages_total"], 6)

    def test_fixture_path_safety(self):
        for unsafe in ("../mailbox_cutover.json", r"..\\mailbox_cutover.json",
                       "C:/mailbox_cutover.json", "/mailbox_cutover.json", "UPPER.json"):
            with self.subTest(unsafe=unsafe), self.assertRaises(inventory.InventoryError) as caught:
                inventory.fixture_path(unsafe)
            self.assertEqual(caught.exception.code, "unsafe_fixture_path")

    def test_fixture_schema_rejects_content_and_tokens(self):
        value = self.fixture()
        value["messages"][0]["content"] = "must never be accepted"
        path = inventory.fixture_path("mailbox_cutover.json")
        with patch.object(inventory, "fixture_path", return_value=path), \
             patch.object(inventory.json, "loads", return_value=value), \
             self.assertRaises(inventory.InventoryError) as caught:
            inventory.load_fixture("mailbox_cutover.json")
        self.assertEqual(caught.exception.code, "invalid_fixture_schema")

    def test_safe_local_target_defaults_to_dry_run_without_connection(self):
        url = "postgresql+psycopg://audit_user:secret@localhost:5432/puw_mailbox_test"
        result, stdout, stderr = self.run_main(
            ["--database-url-env", "PUW_TEST_DATABASE_URL"],
            {"PUW_TEST_DATABASE_URL": url},
        )
        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["mode"], "dry_run")
        self.assertFalse(payload["will_connect"])
        self.assertNotIn("secret", stdout)
        self.assertNotIn("localhost", stdout)
        self.assertNotIn("puw_mailbox_test", stdout)

    def test_production_like_target_refuses_and_redacts(self):
        url = "postgresql://owner:topsecret@db.prod.internal:5432/pu_workspace"
        result, stdout, stderr = self.run_main(
            ["--database-url-env", "PUW_TARGET_URL"],
            {"PUW_TARGET_URL": url},
        )
        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["code"], "production_like_target_refused")
        for secret in ("topsecret", "db.prod.internal", "pu_workspace", "owner"):
            self.assertNotIn(secret, stderr)

    def test_describe_then_exact_production_confirmation_allows_only_dry_run(self):
        url = "postgresql://owner:topsecret@db.prod.internal:5432/pu_workspace"
        env = {"PUW_TARGET_URL": url}
        described = self.run_main(
            ["--database-url-env", "PUW_TARGET_URL", "--describe-target"], env)
        fingerprint = json.loads(described[1])["target_fingerprint"]
        result, stdout, stderr = self.run_main([
            "--database-url-env", "PUW_TARGET_URL", "--allow-production-like",
            "--production-confirmation", f"READ_ONLY_MAILBOX_INVENTORY:{fingerprint}",
        ], env)
        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["mode"], "dry_run")
        self.assertNotIn("topsecret", stdout)

    def test_wrong_confirmation_refuses(self):
        url = "postgresql://owner:secret@localhost/pu_workspace"
        result, _, stderr = self.run_main([
            "--database-url-env", "PUW_TARGET_URL", "--allow-production-like",
            "--production-confirmation", "READ_ONLY_MAILBOX_INVENTORY:wrong",
        ], {"PUW_TARGET_URL": url})
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["code"], "production_like_target_refused")

    def test_execute_requires_separate_opaque_key(self):
        url = "postgresql://owner:secret@localhost/puw_inventory_test"
        result, _, stderr = self.run_main([
            "--database-url-env", "PUW_TARGET_URL", "--execute-read-only",
        ], {"PUW_TARGET_URL": url})
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["code"], "explicit_opaque_key_env_required")

    def test_database_url_is_explicit_and_postgresql_only(self):
        result, _, stderr = self.run_main([])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["code"], "explicit_database_url_env_required")
        result, _, stderr = self.run_main(
            ["--database-url-env", "PUW_TARGET_URL"],
            {"PUW_TARGET_URL": "sqlite:///unsafe.db"},
        )
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["code"], "unsupported_database_scheme")
        result, _, stderr = self.run_main(
            ["--database-url-env", "PUW_TARGET_URL"],
            {"PUW_TARGET_URL": "postgresql://audit:secret@localhost/puw_test?options=unsafe"},
        )
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["code"], "database_url_options_not_allowed")

    def test_fixture_cannot_masquerade_as_database_execution(self):
        result, _, stderr = self.run_main(
            ["--fixture", "mailbox_cutover.json", "--execute-read-only"])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["code"], "fixture_and_database_options_conflict")

    def test_query_allowlist_excludes_content_and_credentials(self):
        statements = []

        class Result:
            def __init__(self, scalar=None):
                self.scalar = scalar
            def scalar_one(self):
                return self.scalar
            def __iter__(self):
                return iter(())

        class Connection:
            def execution_options(self, **_kwargs):
                return self
            def execute(self, statement):
                sql = str(statement)
                statements.append(sql)
                return Result(0 if "count(*) FROM messages" in sql else None)

        snapshot = inventory.safe_metadata_snapshot(Connection(), 100)
        self.assertEqual(snapshot["messages"], [])
        sql = "\n".join(statements).lower()
        for forbidden in (
            "source_sender", "source_name", "source_url", "attachments_json",
            " summary", " context_evidence", "audit_logs.details",
            "access_token", "refresh_token", "normalized_email", "project_contacts.email",
        ):
            self.assertNotIn(forbidden, sql)
        self.assertNotRegex(sql, r"\b(insert|update|delete|alter|drop|truncate|create)\b")

    def test_database_collection_sets_read_only_snapshot_and_rolls_back(self):
        statements = []
        state = {"rolled_back": False, "disposed": False}

        class Result:
            def __init__(self, rows=(), scalar=None):
                self.rows, self.scalar = rows, scalar
            def scalar_one(self):
                return self.scalar
            def __iter__(self):
                return iter(self.rows)

        class Row:
            def __init__(self, value):
                self._mapping = value

        class Transaction:
            def rollback(self):
                state["rolled_back"] = True

        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def begin(self):
                return Transaction()
            def execution_options(self, **_kwargs):
                return self
            def execute(self, statement):
                sql = str(statement).strip()
                statements.append(sql)
                if sql == "SELECT version_num FROM alembic_version":
                    return Result([(inventory.EXPECTED_SCHEMA_HEAD,)])
                if "count(*) FROM messages" in sql:
                    return Result(scalar=0)
                return Result([])

        class Engine:
            def connect(self):
                return Connection()
            def dispose(self):
                state["disposed"] = True

        with patch("sqlalchemy.create_engine", return_value=Engine()):
            snapshot = inventory.collect_database(
                "postgresql://audit:secret@localhost/puw_inventory_test", 100)
        self.assertEqual(snapshot["messages"], [])
        self.assertEqual(
            statements[0],
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )
        self.assertTrue(state["rolled_back"])
        self.assertTrue(state["disposed"])

    def test_partial_origin_is_unresolved_not_guessed(self):
        snapshot = self.fixture()
        snapshot["messages"][0]["source_reference_id"] = None
        report = inventory.make_report(snapshot, b"p" * 32)
        self.assertEqual(report["aggregates"]["origin_unknown_or_partial"], 3)
        self.assertEqual(report["aggregates"]["origin_complete"], 3)
        self.assertEqual(report["aggregates"]["origin_partial_invalid_or_transitional"], 1)

    def test_competing_hypotheses_are_not_reported_as_transfer(self):
        snapshot = self.fixture()
        snapshot["context_relations"].append({
            "id": "relation-hypothesis", "message_id": "m-001",
            "lineage_id": "lineage-hypothesis", "revision": 1,
            "relation_type": "communication.project", "state": "hypothesis",
            "confirmed_by": None, "target_project_id": "project-b",
        })
        report = inventory.make_report(snapshot, b"h" * 32)
        self.assertEqual(
            report["aggregates"]["project_move_proven_by_context_history"], 1)

    def test_reconciliation_oracle_covers_required_scenarios(self):
        path = Path(__file__).resolve().parent / "fixtures" / "cutover_cases.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        ids = [case["case_id"] for case in payload["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {
            "same_provider_id_two_mailboxes", "unknown_legacy_mailbox",
            "one_contact_multiple_projects", "rfc_and_thread_collision",
            "project_transfer_preserves_origin", "ambiguous_mailbox",
        })
        ambiguous = next(c for c in payload["cases"] if c["case_id"] == "ambiguous_mailbox")
        self.assertEqual(ambiguous["expected"], "unresolved")
        self.assertIn("current_oauth", ambiguous["forbidden_evidence"])


if __name__ == "__main__":
    unittest.main()
