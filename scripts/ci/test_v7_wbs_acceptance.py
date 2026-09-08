from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).with_name("v7_wbs_acceptance.py")
SPEC = importlib.util.spec_from_file_location("v7_wbs_acceptance", PATH)
assert SPEC and SPEC.loader
wbs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wbs)


def fixture():
    return wbs._fixture()


def fails(snapshot, code):
    with pytest.raises(wbs.ContractError) as caught:
        wbs.validate_snapshot(snapshot)
    assert caught.value.code == code


def test_canonical_contract_passes():
    assert wbs.validate_snapshot(fixture()) == {"status": "PASS", "nodes": 6, "leaves": 2, "postgres": "CONDITIONAL"}


@pytest.mark.parametrize(("mutation", "code"), [
    (lambda value: value.update(schema_revision="a54f001c0a20"), "schema_revision_mismatch"),
    (lambda value: value["items"][2].update(project_id=999), "cross_scope_row"),
    (lambda value: value["items"][2].update(parent_id="absent"), "missing_parent"),
    (lambda value: value["items"][2].update(parent_id="p"), "invalid_parent_kind"),
    (lambda value: value["items"][5].update(order=0), "non_contiguous_order"),
    (lambda value: value["items"].__setitem__(slice(4, 6), list(reversed(value["items"][4:6]))), "unstable_preorder"),
    (lambda value: value["items"][3].update(finance_link_count=1), "summary_has_leaf_fields"),
    (lambda value: value["items"][5].update(planned_finish="2026-09-06"), "invalid_milestone"),
    (lambda value: value["items"][0].update(progress="57.15"), "rollup_progress_mismatch"),
    (lambda value: value["items"][1].update(planned_finish="2026-09-06"), "rollup_date_mismatch"),
])
def test_snapshot_fails_closed(mutation, code):
    value = fixture()
    mutation(value)
    fails(value, code)


def clone_fixture():
    original = fixture()
    clone = deepcopy(original)
    clone["baseline_id"] = 12
    clone["cloned_from_baseline_id"] = 11
    mapping = {row["id"]: f"c-{row['id']}" for row in clone["items"]}
    for row in clone["items"]:
        old = row["id"]
        row["id"] = mapping[old]
        row["parent_id"] = mapping.get(row["parent_id"])
        row["baseline_id"] = 12
        row["finance_link_count"] = 0
    return original, clone


def test_clone_remaps_identity_and_does_not_copy_finance_links():
    original, clone = clone_fixture()
    assert wbs.validate_clone(original, clone)["identity"] == "remapped"


@pytest.mark.parametrize(("mutation", "code"), [
    (lambda original, clone: (clone.update(baseline_id=11),
                              [row.update(baseline_id=11) for row in clone["items"]]), "clone_scope_mismatch"),
    (lambda original, clone: (clone["items"][0].update(id=original["items"][0]["id"]),
                              clone["items"][1].update(parent_id=original["items"][0]["id"])), "clone_reused_identity"),
    (lambda original, clone: clone["items"][4].update(title="Changed"), "clone_shape_mismatch"),
])
def test_clone_fails_closed(mutation, code):
    original, clone = clone_fixture()
    mutation(original, clone)
    with pytest.raises(wbs.ContractError) as caught:
        wbs.validate_clone(original, clone)
    assert caught.value.code == code


def test_delete_complete_unprotected_subtree_is_allowed():
    value = fixture()
    value["items"][4]["finance_link_count"] = 0
    assert wbs.validate_delete(value, ["sw", "leaf", "ms"])["delete_count"] == 3


@pytest.mark.parametrize(("ids", "mutation", "code"), [
    (["p"], None, "project_root_delete_forbidden"),
    (["sw"], None, "parent_has_surviving_child"),
    (["leaf"], None, "financially_linked_leaf_delete_forbidden"),
    (["ms"], lambda value: value["items"][5].update(has_evidence=True), "historical_row_delete_forbidden"),
    (["missing"], None, "unknown_delete"),
])
def test_delete_guards(ids, mutation, code):
    value = fixture()
    if mutation:
        mutation(value)
    with pytest.raises(wbs.ContractError) as caught:
        wbs.validate_delete(value, ids)
    assert caught.value.code == code


def test_migration_contract_is_sequential_and_additive():
    result = wbs.validate_migration({
        "revision": "a54f001c0a21", "down_revision": "a54f001c0a20",
        "columns": ["wbs_parent_id", "wbs_order", "is_summary"],
        "rewrites_existing_migrations": False,
        "flat_row_policy": "preserve_as_ordered_root_children",
    })
    assert result == {"status": "PASS", "head": "a54f001c0a21", "postgres": "CONDITIONAL"}


def test_postgres_scenarios_cover_concurrency_recovery_and_migration():
    assert len(wbs.POSTGRES_SCENARIOS) == 8
    joined = " ".join(wbs.POSTGRES_SCENARIOS)
    for word in ("upgrade", "concurrent", "clone", "finance", "delete", "restart"):
        assert word in joined
