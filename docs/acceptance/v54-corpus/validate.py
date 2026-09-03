#!/usr/bin/env python3
"""Validate the independent corpus, never execute source text or product code."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path, PurePosixPath

BASE_SHA = "34dcc8306acd6d1bacf85e9ce799330fba907ed9"
CATEGORIES = {"content", "policy", "sequence"}
SCOPES = {"pilot", "integration_blocked", "future_policy_only", "future_fake_only"}
VISIBILITIES = {"sut_permitted", "oracle_only", "oracle_only_after_control_event"}
EMAIL = re.compile(r"[A-Za-z0-9_.+-]+@([A-Za-z0-9.-]+)")
SHA256 = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[a-zA-Z0-9_-]+")


class InvalidCorpus(Exception):
    """Code and safe field location only: no document excerpts."""

    def __init__(self, code, location):
        self.code = code
        self.location = location
        super().__init__(f"{code}: {location}")


def require(condition, code, location):
    if not condition:
        raise InvalidCorpus(code, location)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value, location, allow_empty=False):
    require(isinstance(value, list), "expected_list", location)
    require(allow_empty or bool(value), "empty_list", location)
    require(all(nonempty(x) for x in value), "expected_strings", location)


def object_keys(value, keys, location):
    require(isinstance(value, dict), "expected_object", location)
    require(set(keys) <= set(value), "missing_field", location)


def safe_path(root, relative):
    require(nonempty(relative), "unsafe_path", "path")
    # Also reject Windows syntax when this validator is run on Linux.
    require("\\" not in relative and ":" not in relative, "unsafe_path", "path")
    parts = relative.split("/")
    require(not relative.startswith("/") and all(p not in {"", ".", ".."} for p in parts),
            "unsafe_path", "path")
    require(not PurePosixPath(relative).is_absolute(), "unsafe_path", "path")
    base = root.resolve()
    path = base.joinpath(*parts)
    # Reject all symlinks, including links that currently resolve in the corpus.
    current = base
    for part in parts:
        current = current / part
        require(not current.is_symlink(), "symlink_path", "path")
    try:
        path.resolve().relative_to(base)
    except ValueError:
        raise InvalidCorpus("unsafe_path", "path") from None
    return path


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key", "json")
        result[key] = value
    return result


def reject_constant(_value):
    raise InvalidCorpus("nonfinite_json", "json")


def parse_json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicate_keys,
                          parse_constant=reject_constant)
    except (UnicodeError, ValueError):
        raise InvalidCorpus("invalid_json", "json") from None


def check_addresses(value):
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    require(all(domain.rstrip(".") == "example.test" for domain in EMAIL.findall(text)),
            "non_synthetic_email", "data")


def validate(root, documents=None, source_bytes=None):
    """Overrides are in-memory negative-test inputs, never product fixtures."""
    documents = documents or {}
    source_bytes = source_bytes or {}

    def read_bytes(relative):
        path = safe_path(root, relative)
        try:
            return path.read_bytes()
        except OSError:
            raise InvalidCorpus("unreadable_file", "file") from None

    def read_json(relative):
        safe_path(root, relative)
        result = copy.deepcopy(documents[relative]) if relative in documents else parse_json(read_bytes(relative))
        check_addresses(result)
        return result

    m = read_json("manifest.json")
    object_keys(m, ["format_version", "base_sha", "assets", "entities", "case_files",
                    "declared_counts", "coverage", "requirement_catalog", "interface_requests",
                    "verification", "semantics", "provenance"], "manifest")
    require(m["format_version"] == 1 and m["base_sha"] == BASE_SHA, "wrong_baseline", "manifest")
    require(SHA256.fullmatch(m["provenance"]["requirements"]["sha256"]) is not None,
            "invalid_hash", "requirements")
    require(m["verification"]["application"] == "planned", "unproven_product_pass", "manifest")
    strings(m["case_files"], "case_files")
    require(len(set(m["case_files"])) == len(m["case_files"]), "duplicate_file", "case_files")
    for relative in m["case_files"]:
        safe_path(root, relative)
        require(relative.startswith("cases/") and relative.endswith(".json"),
                "wrong_case_path", "case_files")
    entities = m["entities"]
    object_keys(entities, ["tenants", "actors", "projects", "contracts", "contacts", "connections"], "entities")
    for project in entities["projects"].values():
        require(project["tenant"] in entities["tenants"], "unknown_tenant", "entities")
    for contract in entities["contracts"].values():
        require(contract["project"] in entities["projects"], "unknown_project", "entities")
    for actor in entities["actors"].values():
        require(actor["tenant"] in entities["tenants"], "unknown_tenant", "entities")
    for connection in entities["connections"].values():
        require(connection["tenant"] in entities["tenants"], "unknown_tenant", "entities")
    for contact in entities["contacts"].values():
        require(set(contact["projects"]) <= set(entities["projects"]), "unknown_project", "entities")

    assets, observations, asset_paths, text_by_id = {}, set(), set(), {}
    require(isinstance(m["assets"], list) and bool(m["assets"]), "missing_assets", "manifest")
    for asset in m["assets"]:
        object_keys(asset, ["asset_id", "kind", "logical_source", "observation_id",
                            "observation_revision", "provider_revision", "path", "sha256"], "asset")
        aid = asset["asset_id"]
        require(nonempty(aid) and ID.fullmatch(aid) is not None, "invalid_id", "asset")
        require(aid not in assets, "duplicate_asset", "asset")
        require(asset["observation_id"] not in observations, "duplicate_observation", aid)
        require(type(asset["observation_revision"]) is int and asset["observation_revision"] == 1,
                "invalid_observation_revision", aid)
        require(nonempty(asset["logical_source"]) and nonempty(asset["provider_revision"])
                and nonempty(asset["observation_id"]), "missing_version", aid)
        require(asset["kind"] in {"message", "attachment"}, "invalid_kind", aid)
        relative = asset["path"]
        safe_path(root, relative)
        require(relative.startswith("sources/") and relative.endswith((".txt", ".md")),
                "invalid_source_path", aid)
        require(relative not in asset_paths, "duplicate_asset_path", aid)
        require(isinstance(asset["sha256"], str) and SHA256.fullmatch(asset["sha256"]) is not None,
                "invalid_hash", aid)
        data = source_bytes[relative] if relative in source_bytes else read_bytes(relative)
        require(hashlib.sha256(data).hexdigest() == asset["sha256"], "hash_mismatch", aid)
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            raise InvalidCorpus("invalid_utf8", aid) from None
        require(bool(text) and "\r" not in text and not text.startswith("\ufeff") and text.endswith("\n"),
                "source_encoding_or_eol", aid)
        check_addresses(text)
        assets[aid], text_by_id[aid] = asset, text
        observations.add(asset["observation_id"])
        asset_paths.add(relative)

    cases, counts = {}, Counter()
    for relative in m["case_files"]:
        bundle = read_json(relative)
        object_keys(bundle, ["format_version", "category", "cases"], "case_bundle")
        require(bundle["format_version"] == 1 and bundle["category"] in CATEGORIES,
                "invalid_category", "case_bundle")
        require(isinstance(bundle["cases"], list) and bool(bundle["cases"]), "empty_cases", "case_bundle")
        for c in bundle["cases"]:
            object_keys(c, ["case_id", "category", "requirement", "requirement_refs",
                            "execution_scope", "inputs", "preconditions", "permissions", "sources",
                            "evidence", "events", "expected", "uncertainties", "interface_requests",
                            "verification"], "case")
            cid = c["case_id"]
            require(isinstance(cid, str) and re.fullmatch(r"[CPS][0-9]{2}", cid) is not None,
                    "invalid_case_id", "case")
            require(cid not in cases, "duplicate_case_id", cid)
            require(c["category"] == bundle["category"], "invalid_category", cid)
            require(nonempty(c["requirement"]) and c["execution_scope"] in SCOPES,
                    "missing_requirement_or_scope", cid)
            strings(c["preconditions"], cid)
            strings(c["requirement_refs"], cid)
            strings(c["uncertainties"], cid, allow_empty=True)
            strings(c["interface_requests"], cid, allow_empty=True)
            require(set(c["requirement_refs"]) <= set(m["requirement_catalog"]), "unknown_requirement", cid)
            require(set(c["interface_requests"]) <= set(m["interface_requests"]), "unknown_interface", cid)
            inp = c["inputs"]
            object_keys(inp, ["tenant", "actor", "connection", "source_assets", "external_id",
                             "active_project", "clock", "synthetic_origin"], cid)
            for key, catalog in [("tenant", "tenants"), ("actor", "actors"),
                                 ("connection", "connections"), ("active_project", "projects")]:
                require(inp[key] in entities[catalog], "unknown_entity", cid)
            require(inp["synthetic_origin"] is True and nonempty(inp["external_id"]), "non_synthetic_input", cid)
            for delivery in inp.get("deliveries", []):
                require(delivery["connection"] in entities["connections"] and nonempty(delivery["external_id"]),
                        "invalid_delivery", cid)
            permissions = c["permissions"]
            object_keys(permissions, ["tenant_access", "mailbox_access", "project_access",
                                     "source_read", "evidence_read", "may_confirm_context",
                                     "may_review_claim", "may_approve_action", "autonomy",
                                     "external_actions"], cid)
            require(permissions["autonomy"] == "CONFIRM" and permissions["external_actions"] is False,
                    "unsafe_execution_policy", cid)
            require(set(permissions["project_access"]) <= set(entities["projects"]), "unknown_project", cid)
            require(isinstance(c["sources"], list) and bool(c["sources"]), "missing_case_sources", cid)
            source_ids = []
            for ref in c["sources"]:
                object_keys(ref, ["asset_id", "observation_id"], cid)
                require(ref["asset_id"] in assets, "unknown_source", cid)
                require(ref["observation_id"] == assets[ref["asset_id"]]["observation_id"], "wrong_source_version", cid)
                source_ids.append(ref["asset_id"])
            require(len(source_ids) == len(set(source_ids)), "duplicate_case_source", cid)
            require(inp["source_assets"] == source_ids, "input_source_mismatch", cid)
            require(inp.get("message_asset") in source_ids
                    and assets[inp["message_asset"]]["kind"] == "message", "missing_message_parent", cid)
            require(isinstance(inp.get("attachments"), list), "missing_attachment_links", cid)
            attached = []
            for link in inp["attachments"]:
                object_keys(link, ["asset_id", "parent_message_asset", "display_name", "logical_attachment"], cid)
                require(link["asset_id"] in source_ids and assets[link["asset_id"]]["kind"] == "attachment",
                        "invalid_attachment", cid)
                require(link["parent_message_asset"] == inp["message_asset"]
                        and link["logical_attachment"] == assets[link["asset_id"]]["logical_source"]
                        and nonempty(link["display_name"]), "wrong_attachment_parent", cid)
                attached.append(link["asset_id"])
            require(len(attached) == len(set(attached)) and set(attached) ==
                    {aid for aid in source_ids if assets[aid]["kind"] == "attachment"},
                    "missing_attachment_links", cid)
            require(isinstance(c["evidence"], list) and bool(c["evidence"]), "missing_evidence", cid)
            evidence_ids = set()
            for ev in c["evidence"]:
                object_keys(ev, ["evidence_alias", "asset_id", "observation_id", "start",
                                 "end", "quote", "role", "visibility"], cid)
                require(ev["evidence_alias"] not in evidence_ids, "duplicate_evidence", cid)
                evidence_ids.add(ev["evidence_alias"])
                require(ev["asset_id"] in source_ids, "evidence_source_not_in_case", cid)
                require(ev["observation_id"] == assets[ev["asset_id"]]["observation_id"], "wrong_evidence_version", cid)
                require(ev["visibility"] in VISIBILITIES and nonempty(ev["role"]), "invalid_evidence_metadata", cid)
                text = text_by_id[ev["asset_id"]]
                require(type(ev["start"]) is int and type(ev["end"]) is int
                        and 0 <= ev["start"] < ev["end"] <= len(text), "invalid_offsets", cid)
                require(text[ev["start"]:ev["end"]] == ev["quote"], "excerpt_mismatch", cid)
            require(isinstance(c["events"], list) and bool(c["events"]), "missing_events", cid)
            for index, event in enumerate(c["events"], 1):
                require(event["step"] == index and nonempty(event["event"]), "invalid_event_sequence", cid)
                for key in ["project", "contract"]:
                    if key in event:
                        catalog = entities["projects" if key == "project" else "contracts"]
                        require(event[key] is None or event[key] in catalog, "unknown_event_entity", cid)
                if "asset_id" in event:
                    require(event["asset_id"] in source_ids, "unknown_event_asset", cid)
                for key in ["available_assets", "withhold_assets"]:
                    require(set(event.get(key, [])) <= set(source_ids), "unknown_event_asset", cid)
                if event["event"] == "control_publish_new_source_observation":
                    require(event["previous"] in observations and event["next"] in observations
                            and event["previous"] != event["next"], "invalid_version_event", cid)
                    pair = [a for a in assets.values() if a["observation_id"] in {event["previous"], event["next"]}]
                    require(all(a["logical_source"] == event["logical_source"] and a["asset_id"] in source_ids for a in pair),
                            "invalid_version_lineage", cid)
            expected = c["expected"]
            object_keys(expected, ["hypotheses", "claims", "manual_confirmation", "allowed_changes",
                                   "forbidden_changes", "business", "audit", "pass_conditions"], cid)
            for key in ["allowed_changes", "forbidden_changes", "pass_conditions"]:
                strings(expected[key], cid)
            hypotheses = expected["hypotheses"]
            object_keys(hypotheses, ["project_candidates", "contract_candidates", "relation_state",
                                    "confidence", "confidence_basis", "selection_rule"], cid)
            require(hypotheses["confidence"] is None and hypotheses["confidence_basis"] == "not_measured",
                    "invented_confidence", cid)
            for key, catalog in [("project_candidates", "projects"), ("contract_candidates", "contracts")]:
                strings(hypotheses[key], cid, allow_empty=True)
                require(set(hypotheses[key]) <= set(entities[catalog]), "unknown_candidate", cid)
                require(len(hypotheses[key]) == len(set(hypotheses[key])), "duplicate_candidate", cid)
            for contract_id in hypotheses["contract_candidates"]:
                require(entities["contracts"][contract_id]["project"] in hypotheses["project_candidates"],
                        "incompatible_candidate_pair", cid)
            claims = expected["claims"]
            object_keys(claims, ["kind", "review_state", "evidence_required"], cid)
            if claims["kind"] == "deadline":
                object_keys(claims, ["candidate_dates", "normalized_date", "timestamp"], cid)
                strings(claims["candidate_dates"], cid, allow_empty=True)
                try:
                    for day in claims["candidate_dates"]:
                        require(date.fromisoformat(day).isoformat() == day, "invalid_date", cid)
                    if claims["timestamp"] is not None:
                        timestamp = datetime.fromisoformat(claims["timestamp"])
                        require(timestamp.tzinfo is not None, "timestamp_without_timezone", cid)
                except ValueError:
                    raise InvalidCorpus("invalid_date", cid) from None
                require(claims["normalized_date"] is None or claims["normalized_date"] in claims["candidate_dates"],
                        "unsupported_normalized_date", cid)
            manual = expected["manual_confirmation"]
            object_keys(manual, ["context", "claim", "action"], cid)
            require(all(nonempty(manual[k]) for k in ["context", "claim", "action"]), "missing_approval_rules", cid)
            business = expected["business"]
            object_keys(business, ["phase", "new_tasks", "new_receipts", "external_effects", "confirmed_context"], cid)
            require(nonempty(business["phase"]), "missing_result_phase", cid)
            for key in ["new_tasks", "new_receipts", "external_effects"]:
                require(type(business[key]) is int and business[key] >= 0, "invalid_result_count", cid)
            require(business["external_effects"] == 0, "real_external_effect", cid)
            audit = expected["audit"]
            object_keys(audit, ["required_observations", "forbidden_fields"], cid)
            strings(audit["required_observations"], cid)
            require({"body", "attachment_bytes", "base64", "tokens", "password", "excerpt"} <= set(audit["forbidden_fields"]),
                    "unsafe_audit", cid)
            verification = c["verification"]
            require(verification["structural"] == "validator_required" and verification["application"] == "planned",
                    "unproven_product_pass", cid)
            for key in ["postgres_fault_test", "owner_decision"]:
                require(verification[key] in {"required", "not_required"}, "invalid_gate", cid)
            if any(e["event"].startswith("fault_kill_") for e in c["events"]):
                require(verification["postgres_fault_test"] == "required", "missing_fault_gate", cid)
            if c["inputs"].get("copy_policy") == "no_local_copy":
                require(all(e["visibility"] == "oracle_only" for e in c["evidence"]), "oracle_leak", cid)
                require(not permissions["source_read"] and not permissions["evidence_read"], "oracle_leak", cid)
            cases[cid] = c
            counts[c["category"]] += 1

    require(len(cases) >= 20, "too_few_cases", "corpus")
    require(dict(counts) == {k: v for k, v in m["declared_counts"].items() if k != "total"}
            and len(cases) == m["declared_counts"]["total"], "case_count_mismatch", "manifest")
    require(isinstance(m["coverage"], dict) and bool(m["coverage"]), "missing_coverage", "manifest")
    require(set(m["coverage"].values()) == set(cases), "invalid_coverage", "manifest")
    declared_json = {"manifest.json", *m["case_files"]}
    # Reject undeclared JSON rather than silently ignoring malformed extra cases.
    actual_json = {p.relative_to(root).as_posix() for p in root.rglob("*.json")}
    require(actual_json == declared_json, "undeclared_or_missing_json", "corpus")
    actual_sources = {p.relative_to(root).as_posix() for p in (root / "sources").rglob("*") if p.is_file()}
    require(actual_sources == asset_paths, "undeclared_or_missing_source", "corpus")
    return {"structural": "PASS", "cases": len(cases), "categories": dict(sorted(counts.items())),
            "assets": len(assets), "excerpts": sum(len(c["evidence"]) for c in cases.values()),
            "application": "NOT_RUN", "postgres_fault_tests": "NOT_RUN",
            "postgres_required_cases": sorted(cid for cid, c in cases.items()
                                              if c["verification"]["postgres_fault_test"] == "required"),
            "owner_decision_cases": sorted(cid for cid, c in cases.items()
                                          if c["verification"]["owner_decision"] == "required")}


def self_test(root):
    """Mutation tests in memory. Do not edit corpus, import backend, or make network calls."""
    manifest = parse_json(safe_path(root, "manifest.json").read_bytes())
    docs = {name: parse_json(safe_path(root, name).read_bytes()) for name in manifest["case_files"]}
    docs["manifest.json"] = manifest
    passed = []

    def mutation(name, expected_code, mutate):
        data = copy.deepcopy(docs)
        mutate(data)
        try:
            validate(root, documents=data)
        except InvalidCorpus as exc:
            require(exc.code == expected_code, "wrong_self_test_failure", name)
            passed.append(name)
        else:
            raise InvalidCorpus("mutation_not_detected", name)

    first = lambda d: d["cases/content.json"]["cases"][0]
    mutation("duplicate_case_id", "duplicate_case_id",
             lambda d: d["cases/content.json"]["cases"].append(copy.deepcopy(first(d))))
    mutation("missing_expectations", "missing_field", lambda d: first(d).pop("expected"))
    mutation("empty_pass_condition", "empty_list", lambda d: first(d)["expected"].update(pass_conditions=[]))
    mutation("wrong_hash", "hash_mismatch", lambda d: d["manifest.json"]["assets"][0].update(sha256="0" * 64))
    mutation("wrong_source_version", "wrong_source_version",
             lambda d: first(d)["sources"][0].update(observation_id="obs-absent"))
    mutation("wrong_observation_revision", "invalid_observation_revision",
             lambda d: d["manifest.json"]["assets"][0].update(observation_revision=2))
    mutation("unknown_source", "unknown_source", lambda d: first(d)["sources"][0].update(asset_id="absent"))
    mutation("wrong_excerpt", "excerpt_mismatch", lambda d: first(d)["evidence"][0].update(quote="not the excerpt"))
    mutation("bad_offsets", "invalid_offsets", lambda d: first(d)["evidence"][0].update(start=-1))
    mutation("boolean_offset", "invalid_offsets", lambda d: first(d)["evidence"][0].update(start=True))
    mutation("bad_event_order", "invalid_event_sequence", lambda d: first(d)["events"][0].update(step=2))
    mutation("wrong_attachment_parent", "wrong_attachment_parent",
             lambda d: first(d)["inputs"]["attachments"][0].update(parent_message_asset="absent"))
    mutation("impossible_date", "invalid_date",
             lambda d: first(d)["expected"]["claims"].update(candidate_dates=["2030-02-31"]))
    mutation("missing_fault_gate", "missing_fault_gate",
             lambda d: d["cases/sequence.json"]["cases"][6]["verification"].update(postgres_fault_test="not_required"))
    mutation("numeric_confidence", "invented_confidence",
             lambda d: first(d)["expected"]["hypotheses"].update(confidence=0.99))
    mutation("unknown_candidate", "unknown_candidate",
             lambda d: first(d)["expected"]["hypotheses"].update(contract_candidates=["absent"]))
    mutation("mismatched_contract_project", "incompatible_candidate_pair",
             lambda d: first(d)["expected"]["hypotheses"].update(contract_candidates=["b43"]))
    mutation("source_email_not_synthetic", "non_synthetic_email",
             lambda d: first(d)["inputs"].update(email="fake@invalid.test"))
    mutation("unsafe_real_effect", "real_external_effect",
             lambda d: first(d)["expected"]["business"].update(external_effects=1))
    mutation("forged_app_pass", "unproven_product_pass",
             lambda d: first(d)["verification"].update(application="PASS"))
    mutation("no_copy_oracle_leak", "oracle_leak",
             lambda d: d["cases/policy.json"]["cases"][2]["evidence"][0].update(visibility="sut_permitted"))
    for index, path in enumerate(["../outside.txt", "/outside.txt", "C:/outside.txt",
                                  r"C:\outside.txt", "sources/../../outside.txt", "sources//file.txt"]):
        mutation(f"path_escape_{index}", "unsafe_path",
                 lambda d, p=path: d["manifest.json"]["assets"][0].update(path=p))
    for name, data, code in [("duplicate_json_key", b'{"x":1,"x":2}', "duplicate_json_key"),
                             ("invalid_json", b'{"x":', "invalid_json"),
                             ("nonfinite_json", b'{"x":NaN}', "nonfinite_json")]:
        try:
            parse_json(data)
        except InvalidCorpus as exc:
            require(exc.code == code, "wrong_self_test_failure", name)
            passed.append(name)
        else:
            raise InvalidCorpus("mutation_not_detected", name)
    asset = manifest["assets"][0]
    original = safe_path(root, asset["path"]).read_bytes()
    try:
        validate(root, source_bytes={asset["path"]: original + b"tampered\n"})
    except InvalidCorpus as exc:
        require(exc.code == "hash_mismatch", "wrong_self_test_failure", "source_bytes_changed")
        passed.append("source_bytes_changed")
    else:
        raise InvalidCorpus("mutation_not_detected", "source_bytes_changed")
    return {"negative_checks": len(passed), "result": "PASS", "checks": passed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        result = validate(root)
        if args.self_test:
            result["validator_self_test"] = self_test(root)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except InvalidCorpus as exc:
        print(json.dumps({"structural": "FAIL", "code": exc.code, "location": exc.location}), file=sys.stderr)
        return 1
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        # Malformed data must fail closed without echoing body/credentials.
        print('{"structural":"FAIL","code":"malformed_or_unreadable_corpus"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
