"""Synthetic acceptance for the pure WBS import preview."""

from dataclasses import FrozenInstanceError
import json

import pytest

from app.mvp4.wbs_import import WbsImportError, normalize_wbs_rows, parse_wbs_csv


def row(code, title, duration=None, **extra):
    return {"wbs_code": code, "title": title, "duration_days": duration, **extra}


def synthetic_wbs():
    return [
        row("1", "Preparation", kind="summary", order=20),
        row("1.2", "Permit package", 2, dependencies="1.1FS+1d", order=20),
        row("1.1", "Site survey", 3, order=10),
        row("1.3", "Preparation accepted", kind="milestone", duration=0,
            dependencies=[{"predecessor_ref": "1.2", "link_type": "FF", "lag_days": 0}], order=30),
        row("2", "Construction", kind="summary", order=30),
        row("2.1", "Foundation", 5, dependencies="1.3FS"),
    ]


def assert_error(code, rows, **kwargs):
    with pytest.raises(WbsImportError) as error:
        normalize_wbs_rows(rows, **kwargs)
    assert error.value.code == code
    assert str(error.value) == code


def test_normalizes_hierarchy_kind_order_parent_and_dependencies():
    preview = normalize_wbs_rows(synthetic_wbs())
    assert [(item.client_ref, item.kind, item.parent_ref, item.depth, item.order) for item in preview.rows] == [
        ("wbs_1", "summary", None, 1, 0),
        ("wbs_1_1", "work", "wbs_1", 2, 1),
        ("wbs_1_2", "work", "wbs_1", 2, 2),
        ("wbs_1_3", "milestone", "wbs_1", 2, 3),
        ("wbs_2", "summary", None, 1, 4),
        ("wbs_2_1", "work", "wbs_2", 2, 5),
    ]
    assert preview.rows[2].dependencies[0].predecessor_ref == "wbs_1_1"
    assert (preview.rows[2].dependencies[0].link_type, preview.rows[2].dependencies[0].lag_days) == ("FS", 1)
    assert preview.rows[3].dependencies[0].predecessor_ref == "wbs_1_2"
    assert (preview.summary_count, preview.work_count, preview.milestone_count, preview.max_depth) == (2, 3, 1, 2)
    assert preview.requires_confirmation is True
    assert preview.commit_allowed is preview.originals_changed is False
    json.dumps(preview.as_dict(), ensure_ascii=False)


def test_parent_kind_is_inferred_as_summary_and_leaf_as_work():
    preview = normalize_wbs_rows([row("1", "Top level"), row("1.1", "Leaf work", 1)])
    assert [item.kind for item in preview.rows] == ["summary", "work"]


def test_explicit_parent_accepts_client_ref_without_wbs_codes():
    preview = normalize_wbs_rows([
        {"client_ref": "root", "title": "Root group", "kind": "summary"},
        {"client_ref": "leaf", "parent_ref": "root", "title": "Leaf work", "duration_days": 2},
    ])
    assert preview.rows[1].parent_ref == "root"


def test_forward_parent_reference_is_supported_but_output_is_preorder():
    preview = normalize_wbs_rows([
        {"client_ref": "leaf", "parent_ref": "root", "title": "Leaf work", "duration_days": 2},
        {"client_ref": "root", "title": "Root group", "kind": "summary"},
    ])
    assert [item.client_ref for item in preview.rows] == ["root", "leaf"]


@pytest.mark.parametrize("kind,value,expected", [
    ("Раздел", "summary", None), ("работа", "work", 1), ("ВЕХА", "milestone", 0),
])
def test_russian_kind_aliases(kind, value, expected):
    item = row("1", "Synthetic item", duration=expected, kind=kind)
    preview = normalize_wbs_rows([item])
    assert (preview.rows[0].kind, preview.rows[0].duration_days) == (value, expected)


def test_csv_header_discovery_and_russian_values():
    content = (
        "Report title;;;;;;\n"
        "Код WBS;Наименование;Тип;Длительность дней;Предшественники;Порядок\n"
        "1;Раздел;Раздел;;;20\n"
        "1.2;Монтаж;Работа;2;1.1FS+2д;20\n"
        "1.1;Подготовка;Работа;1;;10\n"
    )
    preview = parse_wbs_csv(content)
    assert [item.client_ref for item in preview.rows] == ["wbs_1", "wbs_1_1", "wbs_1_2"]
    assert preview.rows[-1].dependencies[0].lag_days == 2
    assert [item.source_row for item in preview.rows] == [3, 5, 4]


@pytest.mark.parametrize("bad,code", [
    ([], "empty_wbs"),
    ([row("1.1", "Missing parent", 1)], "missing_parent"),
    ([row("01", "Leading zero", 1)], "invalid_wbs_code"),
    ([row("1.0", "Zero segment", 1)], "invalid_wbs_code"),
    ([row("1", "X", 1)], "invalid_title"),
    ([{"title": "No stable identity", "duration_days": 1}], "invalid_client_ref"),
    ([row("1", "Duplicate one", 1), row("1", "Duplicate two", 1)], "duplicate_client_ref"),
    ([row("1", "Unsupported", 1, kind="phase-ish")], "invalid_wbs_kind"),
    ([row("1", "No duration")], "duration_required"),
    ([row("1", "Zero work", 0)], "invalid_duration"),
    ([row("1", "Negative order", 1, order=-1)], "invalid_wbs_order"),
])
def test_invalid_rows_fail_closed(bad, code):
    assert_error(code, bad)


def test_duplicate_code_with_distinct_explicit_refs_is_rejected():
    assert_error("duplicate_wbs_code", [
        row("1", "First work", 1, client_ref="first"),
        row("1", "Second work", 1, client_ref="second"),
    ])


def test_duplicate_source_rows_are_rejected():
    assert_error("duplicate_source_row", [
        row("1", "First work", 1, source_row=7), row("2", "Second work", 1, source_row=7),
    ])


def test_explicit_and_inferred_parent_must_agree():
    assert_error("parent_conflict", [
        row("1", "First group", kind="summary"), row("2", "Second group", kind="summary"),
        row("1.1", "Child work", 1, parent_ref="2"),
    ])


def test_parent_cycle_is_rejected_without_recursive_overflow():
    assert_error("parent_cycle", [
        {"client_ref": "first", "parent_ref": "second", "title": "First group", "kind": "summary"},
        {"client_ref": "second", "parent_ref": "first", "title": "Second group", "kind": "summary"},
    ])


def test_self_parent_is_rejected():
    assert_error("parent_cycle", [
        {"client_ref": "root", "parent_ref": "root", "title": "Self parent", "kind": "summary"},
    ])


def test_depth_over_five_fails_closed():
    rows = [row(".".join("1" for _ in range(level)), f"Level {level}",
                kind="summary" if level < 6 else "work", duration=None if level < 6 else 1)
            for level in range(1, 7)]
    assert_error("invalid_wbs_code", rows)
    assert_error("wbs_depth_limit", [
        {"client_ref": f"level{level}", "parent_ref": f"level{level - 1}" if level > 1 else None,
         "title": f"Level {level}", "kind": "summary" if level < 6 else "work",
         "duration_days": None if level < 6 else 1}
        for level in range(1, 7)
    ])


@pytest.mark.parametrize("field", ["duration_days", "dependencies"])
def test_summary_cannot_carry_leaf_planning_fields(field):
    value = 1 if field == "duration_days" else "2FS"
    assert_error("summary_has_planning_fields", [
        row("1", "Summary row", kind="summary", **{field: value}), row("2", "Work item", 1),
    ])


@pytest.mark.parametrize("kind,duration", [("milestone", 1), ("milestone", -1), ("work", 0), ("work", 10001)])
def test_kind_duration_contract(kind, duration):
    assert_error("invalid_duration", [row("1", "Bad duration", duration, kind=kind)])


def test_parent_with_children_cannot_be_declared_work_or_milestone():
    for kind, duration in (("work", 1), ("milestone", 0)):
        assert_error("parent_must_be_summary", [
            row("1", "Invalid parent", duration, kind=kind), row("1.1", "Child work", 1),
        ])


@pytest.mark.parametrize("dependencies,code", [
    ("unknownFS", "missing_dependency"),
    ("1FS", "self_dependency"),
    ("2FS;2SS", "duplicate_dependency"),
    ("2XX", "missing_dependency"),
    ("2FS+10001d", "invalid_dependency_syntax"),
])
def test_dependency_rejections(dependencies, code):
    rows = [row("1", "First work", 1, dependencies=dependencies), row("2", "Second work", 1)]
    assert_error(code, rows)


def test_malformed_dependency_rejected_not_partially_parsed():
    assert_error("invalid_dependency_syntax", [row("1", "First work", 1, dependencies="2FS garbage"),
                                                row("2", "Second work", 1)])


def test_dependency_cycle_is_rejected():
    assert_error("dependency_cycle", [
        row("1", "First work", 1, dependencies="2FS"),
        row("2", "Second work", 1, dependencies="3SS"),
        row("3", "Third work", 1, dependencies="1FF"),
    ])


def test_summary_cannot_be_dependency_target():
    assert_error("summary_dependency_forbidden", [
        row("1", "Summary", kind="summary"), row("1.1", "Leaf", 1),
        row("2", "Other leaf", 1, dependencies="1FS"),
    ])


def test_all_link_types_and_signed_lags_are_canonical():
    rows = [row("1", "Root work", 1)]
    for index, (link, lag) in enumerate((("FS", 0), ("SS", 2), ("FF", -2), ("SF", 10)), start=2):
        suffix = "" if lag == 0 else f"{lag:+d}d"
        rows.append(row(str(index), f"Work {index}", 1, dependencies=f"1{link}{suffix}"))
    preview = normalize_wbs_rows(rows)
    assert [(item.dependencies[0].link_type, item.dependencies[0].lag_days) for item in preview.rows[1:]] == [
        ("FS", 0), ("SS", 2), ("FF", -2), ("SF", 10),
    ]


def test_preview_is_deeply_immutable_and_input_is_not_changed():
    source = synthetic_wbs()
    before = json.dumps(source, ensure_ascii=False, sort_keys=True)
    preview = normalize_wbs_rows(source)
    with pytest.raises(FrozenInstanceError):
        preview.rows[0].title = "Changed"
    assert json.dumps(source, ensure_ascii=False, sort_keys=True) == before


def test_limit_and_csv_contract_fail_closed():
    assert_error("wbs_limit", [row(str(index + 1), f"Work {index + 1}", 1) for index in range(2)], max_rows=1)
    with pytest.raises(WbsImportError) as error:
        parse_wbs_csv("Наименование;Длительность\nРабота;1\n")
    assert error.value.code == "required_headers_missing"


def test_csv_duplicate_semantic_header_is_rejected():
    with pytest.raises(WbsImportError) as error:
        parse_wbs_csv("WBS;Код WBS;Наименование;Длительность\n1;1;Работа;1\n")
    assert error.value.code == "duplicate_header"
