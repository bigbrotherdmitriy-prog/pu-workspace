"""Executable, product-independent acceptance contract for the v7 WBS vertical.

The validator consumes a safe JSON projection.  It deliberately imports no
application modules, so it can be added before the product implementation and
can later validate an API response or a PostgreSQL acceptance artifact.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


EXPECTED_PARENT = "a54f001c0a20"
EXPECTED_HEAD = "a54f001c0a21"
KINDS = {"project", "phase", "work", "subwork", "milestone"}
PARENTS = {
    "project": {None},
    "phase": {"project"},
    "work": {"phase"},
    "subwork": {"work", "subwork"},
    "milestone": {"phase", "work", "subwork"},
}
POSTGRES_SCENARIOS = (
    "upgrade_a20_to_a21_on_clean_database",
    "upgrade_a20_to_a21_preserves_flat_rows_as_ordered_roots",
    "concurrent_reparent_uses_graph_revision_cas",
    "concurrent_sibling_reorder_has_one_winner",
    "clone_and_original_are_transactionally_isolated",
    "finance_link_racing_with_summary_conversion_fails_closed",
    "parent_delete_racing_with_child_insert_fails_closed",
    "restart_preserves_hierarchy_and_rollups",
)


class ContractError(ValueError):
    """Stable, content-free contract failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise ContractError(code)


def _day(value: Any) -> date:
    if not isinstance(value, str):
        _fail("invalid_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail("invalid_date")


def _progress(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _fail("invalid_progress")
    try:
        result = Decimal(str(value))
    except Exception:
        _fail("invalid_progress")
    if result < 0 or result > 100:
        _fail("invalid_progress")
    return result


def _weight(row: dict[str, Any]) -> Decimal:
    if row["kind"] == "milestone":
        return Decimal(1)
    duration = row.get("duration_days")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
        _fail("invalid_leaf_duration")
    return Decimal(duration)


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate hierarchy plus exact derived fields and return safe counts."""
    if snapshot.get("schema_revision") != EXPECTED_HEAD:
        _fail("schema_revision_mismatch")
    if not isinstance(snapshot.get("project_id"), int) or not isinstance(snapshot.get("baseline_id"), int):
        _fail("invalid_scope")
    if not isinstance(snapshot.get("graph_revision"), int) or snapshot["graph_revision"] < 1:
        _fail("invalid_graph_revision")
    rows = snapshot.get("items")
    if not isinstance(rows, list) or not rows:
        _fail("empty_wbs")
    if any(not isinstance(row, dict) for row in rows):
        _fail("invalid_row")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(value, str) or not value or "/" in value or "\\" in value for value in ids):
        _fail("invalid_id")
    if len(set(ids)) != len(ids):
        _fail("duplicate_id")
    by_id = dict(zip(ids, rows))
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    roots = []
    for row in rows:
        kind = row.get("kind")
        parent_id = row.get("parent_id")
        if kind not in KINDS:
            _fail("invalid_kind")
        if parent_id is None:
            roots.append(row)
            parent_kind = None
        else:
            parent = by_id.get(parent_id)
            if parent is None:
                _fail("missing_parent")
            parent_kind = parent.get("kind")
            children[parent_id].append(row)
        if parent_kind not in PARENTS[kind]:
            _fail("invalid_parent_kind")
        if row.get("project_id") != snapshot["project_id"] or row.get("baseline_id") != snapshot["baseline_id"]:
            _fail("cross_scope_row")
    if len(roots) != 1 or roots[0].get("kind") != "project":
        _fail("single_project_root_required")
    if roots[0].get("order") != 0:
        _fail("invalid_root_order")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(row_id: str) -> None:
        if row_id in visiting:
            _fail("hierarchy_cycle")
        if row_id in visited:
            return
        visiting.add(row_id)
        for child in children[row_id]:
            visit(child["id"])
        visiting.remove(row_id)
        visited.add(row_id)

    visit(roots[0]["id"])
    if len(visited) != len(rows):
        _fail("disconnected_hierarchy")
    for sibling_rows in children.values():
        orders = [row.get("order") for row in sibling_rows]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in orders):
            _fail("invalid_order")
        if sorted(orders) != list(range(len(orders))):
            _fail("non_contiguous_order")

    preorder: list[str] = []

    def append_preorder(row: dict[str, Any]) -> None:
        preorder.append(row["id"])
        for child in sorted(children[row["id"]], key=lambda value: value["order"]):
            append_preorder(child)

    append_preorder(roots[0])
    if ids != preorder:
        _fail("unstable_preorder")

    canonical = False
    leaf_count = 0

    def derive(row: dict[str, Any], lineage: tuple[str, ...]) -> tuple[date, date, Decimal, Decimal]:
        nonlocal canonical, leaf_count
        descendants = children[row["id"]]
        next_lineage = lineage + (row["kind"],)
        finance_links = row.get("finance_link_count", 0)
        if isinstance(finance_links, bool) or not isinstance(finance_links, int) or finance_links < 0:
            _fail("invalid_finance_link_count")
        if not descendants:
            leaf_count += 1
            if row["kind"] in {"project", "phase"}:
                _fail("container_must_have_child")
            start, finish = _day(row.get("planned_start")), _day(row.get("planned_finish"))
            if finish < start:
                _fail("finish_before_start")
            weight = _weight(row)
            if row["kind"] == "milestone":
                if start != finish or row.get("duration_days") != 0:
                    _fail("invalid_milestone")
                if next_lineage[-5:] == ("project", "phase", "work", "subwork", "milestone"):
                    canonical = True
            progress = _progress(row.get("progress"))
            return start, finish, progress * weight, weight

        if row.get("duration_days") is not None or finance_links != 0:
            _fail("summary_has_leaf_fields")
        if row.get("dependencies") not in (None, []):
            _fail("summary_has_dependency")
        derived = [derive(child, next_lineage) for child in descendants]
        start = min(value[0] for value in derived)
        finish = max(value[1] for value in derived)
        numerator = sum((value[2] for value in derived), Decimal(0))
        denominator = sum((value[3] for value in derived), Decimal(0))
        progress = (numerator / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if _day(row.get("planned_start")) != start or _day(row.get("planned_finish")) != finish:
            _fail("rollup_date_mismatch")
        if _progress(row.get("progress")) != progress:
            _fail("rollup_progress_mismatch")
        return start, finish, numerator, denominator

    derive(roots[0], ())
    if not canonical:
        _fail("canonical_depth_not_demonstrated")
    return {"status": "PASS", "nodes": len(rows), "leaves": leaf_count, "postgres": "CONDITIONAL"}


def validate_clone(original: dict[str, Any], clone: dict[str, Any]) -> dict[str, Any]:
    validate_snapshot(original)
    validate_snapshot(clone)
    if clone.get("cloned_from_baseline_id") != original.get("baseline_id"):
        _fail("clone_origin_mismatch")
    if clone.get("baseline_id") == original.get("baseline_id") or clone.get("project_id") != original.get("project_id"):
        _fail("clone_scope_mismatch")
    original_ids = {row["id"] for row in original["items"]}
    clone_ids = {row["id"] for row in clone["items"]}
    if original_ids & clone_ids:
        _fail("clone_reused_identity")

    def shape(snapshot: dict[str, Any]) -> list[tuple[Any, ...]]:
        rows = snapshot["items"]
        by_id = {row["id"]: row for row in rows}
        paths: dict[str, tuple[int, ...]] = {}
        for row in rows:
            paths[row["id"]] = () if row["parent_id"] is None else paths[row["parent_id"]] + (row["order"],)
        return [(paths[row["id"]], row["kind"], row.get("title"), row.get("duration_days"),
                 row.get("planned_start"), row.get("planned_finish"), str(row.get("progress"))) for row in rows]

    if shape(original) != shape(clone):
        _fail("clone_shape_mismatch")
    if any(row.get("finance_link_count", 0) for row in clone["items"]):
        _fail("clone_copied_finance_links")
    return {"status": "PASS", "identity": "remapped", "finance_links": "not_copied"}


def validate_delete(snapshot: dict[str, Any], delete_ids: list[str]) -> dict[str, Any]:
    validate_snapshot(snapshot)
    if len(delete_ids) != len(set(delete_ids)):
        _fail("duplicate_delete")
    by_id = {row["id"]: row for row in snapshot["items"]}
    selected = set(delete_ids)
    if not selected or not selected <= set(by_id):
        _fail("unknown_delete")
    if any(row["parent_id"] is None for row in (by_id[value] for value in selected)):
        _fail("project_root_delete_forbidden")
    for row in snapshot["items"]:
        if row.get("parent_id") in selected and row["id"] not in selected:
            _fail("parent_has_surviving_child")
    for row_id in selected:
        row = by_id[row_id]
        if row.get("finance_link_count", 0):
            _fail("financially_linked_leaf_delete_forbidden")
        if row.get("has_actuals") or row.get("has_evidence"):
            _fail("historical_row_delete_forbidden")
    return {"status": "PASS", "delete_count": len(selected)}


def validate_migration(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("down_revision") != EXPECTED_PARENT or manifest.get("revision") != EXPECTED_HEAD:
        _fail("migration_lineage_mismatch")
    columns = manifest.get("columns")
    expected = {"wbs_parent_id", "wbs_order", "is_summary"}
    if not isinstance(columns, list) or not expected <= set(columns):
        _fail("migration_columns_missing")
    if manifest.get("rewrites_existing_migrations") is not False:
        _fail("historical_migration_rewrite")
    if manifest.get("flat_row_policy") != "preserve_as_ordered_root_children":
        _fail("flat_row_policy_missing")
    return {"status": "PASS", "head": EXPECTED_HEAD, "postgres": "CONDITIONAL"}


def _fixture() -> dict[str, Any]:
    common = {"project_id": 7, "baseline_id": 11, "finance_link_count": 0, "has_actuals": False,
              "has_evidence": False, "dependencies": []}
    def row(identifier: str, kind: str, parent: str | None, order: int, title: str, start: str,
            finish: str, progress: Any, duration: int | None = None, links: int = 0) -> dict[str, Any]:
        return {**common, "id": identifier, "kind": kind, "parent_id": parent, "order": order,
                "title": title, "planned_start": start, "planned_finish": finish, "progress": progress,
                "duration_days": duration, "finance_link_count": links}
    return {"schema_revision": EXPECTED_HEAD, "project_id": 7, "baseline_id": 11, "graph_revision": 3,
            "items": [
                row("p", "project", None, 0, "Synthetic project", "2026-09-01", "2026-09-05", "57.14"),
                row("ph", "phase", "p", 0, "Synthetic phase", "2026-09-01", "2026-09-05", "57.14"),
                row("w", "work", "ph", 0, "Synthetic work", "2026-09-01", "2026-09-05", "57.14"),
                row("sw", "subwork", "w", 0, "Synthetic subwork", "2026-09-01", "2026-09-05", "57.14"),
                row("leaf", "subwork", "sw", 0, "Synthetic leaf", "2026-09-01", "2026-09-03", 50, 6, 1),
                row("ms", "milestone", "sw", 1, "Synthetic milestone", "2026-09-05", "2026-09-05", 100, 0),
            ]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", nargs="?", type=Path)
    parser.add_argument("--write-example", type=Path)
    args = parser.parse_args()
    if args.write_example:
        args.write_example.write_text(json.dumps(_fixture(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot else _fixture()
    print(json.dumps(validate_snapshot(snapshot), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
