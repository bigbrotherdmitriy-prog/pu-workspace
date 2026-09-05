#!/usr/bin/env python3
"""Read-only, PII-free inventory for a future mailbox cutover test database."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

TOOL_VERSION = "1"
EXPECTED_SCHEMA_HEAD = "a54f001c0a04"
DEFAULT_LIMIT = 100_000
TEST_MARKERS = ("test", "testing", "ci", "staging", "stage", "dev", "local", "sandbox")
PROD_MARKERS = ("prod", "production", "live")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,127}")
GMAIL_TYPES = {"email", "email_outgoing"}
FIXTURE_KEYS = {"fixture_version", "messages", "tasks", "drafts", "completion_suggestions",
                "context_relations", "audit_events", "contacts"}
MESSAGE_KEYS = {"id", "organization_id", "project_id", "source_type", "source_external_id",
                "mail_connection_id", "provider_message_id", "source_reference_id",
                "source_thread_id", "context_confirmed"}
RELATED_KEYS = {"id", "project_id", "message_id"}
CONTEXT_KEYS = {"id", "message_id", "lineage_id", "revision", "relation_type", "state",
                "confirmed_by", "target_project_id"}
AUDIT_KEYS = {"id", "action", "entity_type", "entity_id"}
CONTACT_KEYS = {"id", "organization_id", "project_id", "identity_key"}
FIXTURE_DIR = Path(__file__).resolve().parent / "tests" / "fixtures"


class InventoryError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(value: bool, code: str) -> None:
    if not value:
        raise InventoryError(code)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def opaque(key: bytes, kind: str, value: object) -> str:
    digest = hmac.new(key, f"{kind}\0{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    return f"{kind}_{digest}"


def target_policy(database_url: str) -> dict:
    """Classify without returning URL, credentials, host or database name."""
    try:
        parsed = urlsplit(database_url)
        scheme = parsed.scheme.split("+", 1)[0].lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        database = unquote(parsed.path.lstrip("/")).lower()
        username_present = parsed.username is not None
        port = parsed.port or 5432
    except (TypeError, ValueError):
        raise InventoryError("invalid_database_url") from None
    require(scheme in {"postgresql", "postgres"}, "unsupported_database_scheme")
    require(bool(host and database and username_present), "incomplete_database_target")
    require(not parsed.query and not parsed.fragment, "database_url_options_not_allowed")
    require(re.fullmatch(r"[a-z0-9_-]+", database) is not None, "unsafe_database_name")
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    reasons: list[str] = []
    if any(marker in host or marker in database for marker in PROD_MARKERS):
        reasons.append("production_marker")
    if not any(marker in database for marker in TEST_MARKERS):
        reasons.append("database_name_not_explicitly_test_like")
    if not local:
        reasons.append("non_loopback_host")
    fingerprint_input = f"{scheme}|{host}|{port}|{database}|{parsed.username or ''}"
    fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:20]
    return {"target_fingerprint": fingerprint, "production_like": bool(reasons),
            "reasons": sorted(reasons), "local": local}


def require_production_gate(policy: dict, allow: bool, confirmation: str | None) -> None:
    if not policy["production_like"]:
        return
    expected = f"READ_ONLY_MAILBOX_INVENTORY:{policy['target_fingerprint']}"
    require(allow and hmac.compare_digest(confirmation or "", expected),
            "production_like_target_refused")


def fixture_path(name: str) -> Path:
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.json", name or "")), "unsafe_fixture_path")
    base = FIXTURE_DIR.resolve()
    path = (base / name).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        raise InventoryError("unsafe_fixture_path") from None
    require(not path.is_symlink() and path.is_file(), "fixture_not_found")
    return path


def strict_object(value: object, keys: set[str], code: str) -> dict:
    require(isinstance(value, dict) and set(value) == keys, code)
    return value


def load_fixture(name: str) -> dict:
    path = fixture_path(name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise InventoryError("invalid_fixture") from None
    strict_object(value, FIXTURE_KEYS, "invalid_fixture_schema")
    require(value["fixture_version"] == 1, "invalid_fixture_version")
    specs = [
        ("messages", MESSAGE_KEYS), ("tasks", RELATED_KEYS), ("drafts", RELATED_KEYS),
        ("completion_suggestions", RELATED_KEYS), ("context_relations", CONTEXT_KEYS),
        ("audit_events", AUDIT_KEYS), ("contacts", CONTACT_KEYS),
    ]
    for collection, keys in specs:
        require(isinstance(value[collection], list), "invalid_fixture_schema")
        for row in value[collection]:
            strict_object(row, keys, "invalid_fixture_schema")
    return value


def safe_metadata_snapshot(connection, max_messages: int) -> dict:
    """Read allow-listed metadata only. Never select body/subject/address/token/detail."""
    try:
        from sqlalchemy import text
    except ImportError:
        raise InventoryError("sqlalchemy_unavailable") from None

    total = int(connection.execute(text(
        "SELECT count(*) FROM messages WHERE source_type IN ('email','email_outgoing')"
    )).scalar_one())
    require(total <= max_messages, "message_limit_exceeded")

    def rows(sql: str) -> list[dict]:
        result = connection.execution_options(stream_results=True).execute(text(sql))
        return [dict(row._mapping) for row in result]

    return {
        "fixture_version": 1,
        "messages": rows("""
            SELECT id, organization_id, project_id, source_type, source_external_id,
                   mail_connection_id, provider_message_id, source_reference_id,
                   source_thread_id, context_confirmed
            FROM messages
            WHERE source_type IN ('email','email_outgoing')
            ORDER BY id
        """),
        "tasks": rows("""
            SELECT r.id, r.project_id, r.message_id FROM tasks AS r
            JOIN messages AS m ON m.id = r.message_id
            WHERE m.source_type IN ('email','email_outgoing') ORDER BY r.id
        """),
        "drafts": rows("""
            SELECT r.id, r.project_id, r.message_id FROM response_drafts AS r
            JOIN messages AS m ON m.id = r.message_id
            WHERE m.source_type IN ('email','email_outgoing') ORDER BY r.id
        """),
        "completion_suggestions": rows("""
            SELECT r.id, r.project_id, r.message_id FROM task_completion_suggestions AS r
            JOIN messages AS m ON m.id = r.message_id
            WHERE m.source_type IN ('email','email_outgoing') ORDER BY r.id
        """),
        "context_relations": rows("""
            SELECT r.id, r.message_id, r.lineage_id, r.revision, r.relation_type,
                   r.state, r.confirmed_by, r.target_ref #>> '{id,value}' AS target_project_id
            FROM v54_context_relations AS r
            JOIN messages AS m ON m.id = r.message_id
            WHERE r.relation_type = 'communication.project'
              AND m.source_type IN ('email','email_outgoing')
            ORDER BY r.message_id, r.lineage_id, r.revision, r.id
        """),
        "audit_events": rows("""
            SELECT a.id, a.action, a.entity_type, a.entity_id FROM audit_logs AS a
            JOIN messages AS m ON m.id = a.entity_id AND a.entity_type = 'message'
            WHERE a.action IN ('message_context_confirmed','message_context_bulk_confirmed')
              AND m.source_type IN ('email','email_outgoing')
            ORDER BY a.id
        """),
        # There is no direct Message->ProjectContact relationship. Reading email
        # to fabricate one is forbidden, so current DB contact linkage is explicit
        # "not measurable". Offline post-expand fixtures may supply identity_key.
        "contacts": [],
    }


def collect_database(database_url: str, max_messages: int) -> dict:
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        raise InventoryError("sqlalchemy_unavailable") from None
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                connection.execute(text("SET LOCAL lock_timeout = '3s'"))
                heads = sorted(str(row[0]) for row in connection.execute(
                    text("SELECT version_num FROM alembic_version")))
                require(heads == [EXPECTED_SCHEMA_HEAD], "schema_head_mismatch")
                snapshot = safe_metadata_snapshot(connection, max_messages)
            finally:
                transaction.rollback()
            return snapshot
    except InventoryError:
        raise
    except Exception:
        # DB/driver messages can contain URLs, SQL values or identifiers.
        raise InventoryError("database_inventory_failed") from None
    finally:
        engine.dispose()


def make_report(snapshot: dict, opaque_key: bytes, sample_limit: int = 20) -> dict:
    messages = sorted(snapshot["messages"], key=lambda row: str(row["id"]))
    gmail = [m for m in messages if m["source_type"] in GMAIL_TYPES]
    message_by_id = {str(m["id"]): m for m in gmail}
    require(len(message_by_id) == len(gmail), "duplicate_message_id")

    def msg_id(value: object) -> str:
        return opaque(opaque_key, "msg", value)

    complete, unknown, all_missing, partial = [], [], [], []
    for message in gmail:
        fields = (message["mail_connection_id"], message["provider_message_id"],
                  message["source_reference_id"])
        if all(fields):
            complete.append(message)
        else:
            unknown.append(message)
            (all_missing if not any(fields) else partial).append(message)

    def groups(rows: list[dict], key_fn, minimum: int = 2) -> list[list[dict]]:
        grouped: dict[object, list[dict]] = defaultdict(list)
        for row in rows:
            key = key_fn(row)
            if key is not None:
                grouped[key].append(row)
        return sorted((sorted(group, key=lambda r: str(r["id"]))
                       for group in grouped.values() if len(group) >= minimum),
                      key=lambda group: tuple(str(r["id"]) for r in group))

    legacy_collision = groups(gmail, lambda m: (m["source_type"], m["source_external_id"]))
    provider_across_types = [g for g in groups(gmail, lambda m: m["source_external_id"])
                             if len({m["source_type"] for m in g}) > 1]
    mailbox_collision = groups(complete, lambda m: (str(m["mail_connection_id"]),
                                                    m["provider_message_id"]))
    thread_groups = groups([m for m in gmail if m["source_thread_id"]],
                           lambda m: m["source_thread_id"])
    thread_cross_mailbox = [g for g in thread_groups
                            if len({str(m["mail_connection_id"]) for m in g
                                    if m["mail_connection_id"]}) > 1]
    thread_unknown = [g for g in thread_groups if any(not m["mail_connection_id"] for m in g)]

    relation_rows = [r for r in snapshot["context_relations"]
                     if str(r["message_id"]) in message_by_id and
                     r["relation_type"] == "communication.project"]
    relation_projects: dict[str, set[str]] = defaultdict(set)
    confirmed_approver: set[str] = set()
    for row in relation_rows:
        mid = str(row["message_id"])
        # Hypotheses for two projects are ambiguity, not a historical transfer.
        if row["state"] in {"confirmed", "superseded"} and row["target_project_id"] is not None:
            relation_projects[mid].add(str(row["target_project_id"]))
        if row["state"] == "confirmed" and row["confirmed_by"] is not None:
            current_project = str(message_by_id[mid]["project_id"])
            if str(row["target_project_id"]) == current_project:
                confirmed_approver.add(mid)
    proven_moved = sorted(mid for mid, values in relation_projects.items() if len(values) > 1)

    audit_counts = Counter(str(r["entity_id"]) for r in snapshot["audit_events"]
                           if r["entity_id"] is not None and str(r["entity_id"]) in message_by_id)
    reconfirmed = sorted(mid for mid, count in audit_counts.items() if count > 1)
    confirmed = [m for m in gmail if bool(m["context_confirmed"])]
    without_approver = [m for m in confirmed if str(m["id"]) not in confirmed_approver]

    def related(name: str) -> tuple[list[dict], list[dict]]:
        rows = [r for r in snapshot[name] if str(r["message_id"]) in message_by_id]
        mismatched = [r for r in rows
                      if str(r["project_id"]) != str(message_by_id[str(r["message_id"])]["project_id"])]
        return rows, mismatched

    tasks, task_mismatch = related("tasks")
    drafts, draft_mismatch = related("drafts")
    suggestions, suggestion_mismatch = related("completion_suggestions")
    contact_rows = snapshot["contacts"]
    contact_identity_groups = groups(
        [r for r in contact_rows if r.get("identity_key") is not None],
        lambda r: (r["organization_id"], r["identity_key"]),
    )
    multiproject_contacts = [g for g in contact_identity_groups
                             if len({str(r["project_id"]) for r in g}) > 1]

    def group_samples(kind: str, grouped: list[list[dict]]) -> list[dict]:
        return [{"group_id": opaque(opaque_key, kind, ",".join(str(r["id"]) for r in group)),
                 "message_count": len(group),
                 "message_ids": [msg_id(r["id"]) for r in group[:sample_limit]]}
                for group in grouped[:sample_limit]]

    report = {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "aggregates": {
            "gmail_messages_total": len(gmail),
            "origin_complete": len(complete),
            "origin_unknown_or_partial": len(unknown),
            "messages_without_mailbox_provenance":
                sum(1 for m in gmail if not m["mail_connection_id"]),
            "messages_without_provider_message_id":
                sum(1 for m in gmail if not m["provider_message_id"]),
            "messages_without_source_reference":
                sum(1 for m in gmail if not m["source_reference_id"]),
            "origin_all_fields_missing": len(all_missing),
            "origin_partial_invalid_or_transitional": len(partial),
            "legacy_source_key_collision_groups": len(legacy_collision),
            "legacy_source_key_collision_messages": sum(len(g) for g in legacy_collision),
            "provider_id_across_source_type_groups": len(provider_across_types),
            "mailbox_scoped_key_collision_groups": len(mailbox_collision),
            "thread_cross_mailbox_groups": len(thread_cross_mailbox),
            "thread_with_unknown_mailbox_groups": len(thread_unknown),
            "project_move_proven_by_context_history": len(proven_moved),
            "legacy_reconfirmation_signal_not_proof": len(reconfirmed),
            "confirmed_context_total": len(confirmed),
            "confirmed_context_with_recorded_v54_approver":
                len(confirmed) - len(without_approver),
            "confirmed_context_without_recorded_v54_approver": len(without_approver),
            # A user FK alone does not prove a human decision: current User rows
            # do not carry a trusted human/service-principal classification.
            "confirmed_context_without_trustworthy_human_proof": len(confirmed),
            "tasks_linked": len(tasks),
            "tasks_project_mismatch": len(task_mismatch),
            "drafts_linked": len(drafts),
            "drafts_project_mismatch": len(draft_mismatch),
            "completion_suggestions_linked": len(suggestions),
            "completion_suggestions_project_mismatch": len(suggestion_mismatch),
            "explicit_contact_link_rows_if_provided": len(contact_rows),
            "contact_identity_multiproject_groups_if_explicit_key_exists":
                len(multiproject_contacts),
            "moved_messages_with_current_origin_but_no_origin_history_proof":
                sum(1 for mid in proven_moved if message_by_id[mid]["mail_connection_id"]),
        },
        "samples": {
            "origin_unknown_message_ids": [msg_id(m["id"]) for m in unknown[:sample_limit]],
            "legacy_collision_groups": group_samples("legacy_group", legacy_collision),
            "provider_across_type_groups": group_samples("provider_group", provider_across_types),
            "mailbox_collision_groups": group_samples("mailbox_group", mailbox_collision),
            "thread_cross_mailbox_groups": group_samples("thread_group", thread_cross_mailbox),
            "thread_unknown_mailbox_groups": group_samples("thread_unknown", thread_unknown),
            "proven_moved_message_ids": [msg_id(mid) for mid in proven_moved[:sample_limit]],
            "legacy_reconfirmation_message_ids": [msg_id(mid) for mid in reconfirmed[:sample_limit]],
            "confirmed_without_recorded_v54_approver_message_ids":
                [msg_id(m["id"]) for m in without_approver[:sample_limit]],
            "confirmed_without_trustworthy_human_proof_message_ids":
                [msg_id(m["id"]) for m in confirmed[:sample_limit]],
            "task_project_mismatch_message_ids":
                sorted({msg_id(r["message_id"]) for r in task_mismatch})[:sample_limit],
            "draft_project_mismatch_message_ids":
                sorted({msg_id(r["message_id"]) for r in draft_mismatch})[:sample_limit],
            "completion_project_mismatch_message_ids":
                sorted({msg_id(r["message_id"]) for r in suggestion_mismatch})[:sample_limit],
        },
        "limitations": {
            "contact_linkage": "not_measurable_without_approved_non_PII_relationship",
            "legacy_project_move": "audit_reconfirmation_is_signal_not_before_after_proof",
            "source_connection_preserved_after_move":
                "current_origin_can_be_seen_but_immutable_origin_history_is_required_for_proof",
            "oauth_to_mailbox": "project_scoped_google_token_is_not_stable_mailbox_identity",
            "human_approver": "confirmed_by_user_id_does_not_prove_human_actor_without_authority_registry",
            "rfc_message_identity": "legacy_schema_has_no_rfc_message_id_or_references_fields",
        },
    }
    report["report_id"] = opaque(opaque_key, "report", canonical_json(report))
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--database-url-env", help="Name of explicitly supplied environment variable")
    result.add_argument("--opaque-key-env", help="Environment variable with >=32-char report HMAC key")
    result.add_argument("--execute-read-only", action="store_true",
                        help="Connect and execute metadata-only transaction; default is dry-run")
    result.add_argument("--describe-target", action="store_true",
                        help="Print only target fingerprint/classification; never connect")
    result.add_argument("--allow-production-like", action="store_true")
    result.add_argument("--production-confirmation")
    result.add_argument("--max-messages", type=int, default=DEFAULT_LIMIT)
    result.add_argument("--fixture", help="Offline fixture filename from fixed tests/fixtures directory")
    result.add_argument("--sample-limit", type=int, default=20)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        require(1 <= args.sample_limit <= 100, "invalid_sample_limit")
        require(1 <= args.max_messages <= 1_000_000, "invalid_message_limit")
        if args.fixture:
            require(not args.database_url_env and not args.describe_target and
                    not args.allow_production_like and not args.production_confirmation
                    and not args.execute_read_only,
                    "fixture_and_database_options_conflict")
            snapshot = load_fixture(args.fixture)
            key = b"synthetic-fixture-opaque-key-v1-not-a-secret"
            if args.opaque_key_env:
                require(ENV_NAME.fullmatch(args.opaque_key_env) is not None, "invalid_env_name")
                key_value = os.environ.get(args.opaque_key_env)
                require(key_value is not None and len(key_value) >= 32, "missing_or_short_opaque_key")
                key = key_value.encode("utf-8")
            output = {"status": "ok", "mode": "offline_fixture", "fixture": args.fixture,
                      "report": make_report(snapshot, key, args.sample_limit)}
            print(canonical_json(output))
            return 0

        require(args.database_url_env is not None and
                ENV_NAME.fullmatch(args.database_url_env) is not None, "explicit_database_url_env_required")
        database_url = os.environ.get(args.database_url_env)
        require(database_url is not None and bool(database_url.strip()), "database_url_env_missing")
        policy = target_policy(database_url)
        if args.describe_target:
            print(canonical_json({"status": "ok", "mode": "describe_only", **policy}))
            return 0
        require_production_gate(policy, args.allow_production_like, args.production_confirmation)
        if not args.execute_read_only:
            print(canonical_json({
                "status": "ok", "mode": "dry_run", "will_connect": False,
                **policy,
                "next_step": "repeat_with_execute_read_only_and_opaque_key_env",
                "selected_columns": {
                    "messages": ["id", "organization_id", "project_id", "source_type",
                                 "source_external_id", "mail_connection_id", "provider_message_id",
                                 "source_reference_id", "source_thread_id", "context_confirmed"],
                    "excluded": ["content", "source_name", "source_sender", "source_url",
                                 "attachments_json", "summary", "context_evidence", "audit.details",
                                 "contact.email", "contact.normalized_email", "credentials", "tokens"],
                },
            }))
            return 0
        require(args.opaque_key_env is not None and
                ENV_NAME.fullmatch(args.opaque_key_env) is not None, "explicit_opaque_key_env_required")
        key_value = os.environ.get(args.opaque_key_env)
        require(key_value is not None and len(key_value) >= 32, "missing_or_short_opaque_key")
        snapshot = collect_database(database_url, args.max_messages)
        output = {"status": "ok", "mode": "read_only_inventory",
                  "target_fingerprint": policy["target_fingerprint"],
                  "production_like": policy["production_like"],
                  "report": make_report(snapshot, key_value.encode("utf-8"), args.sample_limit)}
        print(canonical_json(output))
        return 0
    except InventoryError as exc:
        print(canonical_json({"status": "refused", "code": exc.code}), file=sys.stderr)
        return 2
    except Exception:
        print(canonical_json({"status": "failed", "code": "safe_unexpected_error"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
