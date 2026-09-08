"""Pure, fail-closed WBS schedule import preview.

This module deliberately has no database or API dependency.  It turns an
explicit CSV/table representation into a complete, normalized hierarchy that an
integrator may later translate to the authoritative schedule graph endpoint.
Nothing in this module commits, mutates a source document, or invents missing
parents, durations or dependency targets.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass


class WbsImportError(ValueError):
    """Stable rejection containing only a code and synthetic row positions."""

    def __init__(self, code: str, source_rows: Iterable[int] = ()):
        self.code = code
        self.source_rows = tuple(sorted(set(source_rows)))
        super().__init__(code)


@dataclass(frozen=True)
class WbsDependency:
    predecessor_ref: str
    link_type: str = "FS"
    lag_days: int = 0


@dataclass(frozen=True)
class WbsPreviewRow:
    source_row: int
    client_ref: str
    wbs_code: str | None
    parent_ref: str | None
    depth: int
    order: int
    sibling_order: int
    kind: str
    title: str
    duration_days: int | None
    dependencies: tuple[WbsDependency, ...]


@dataclass(frozen=True)
class WbsPreview:
    rows: tuple[WbsPreviewRow, ...]
    max_depth: int
    summary_count: int
    work_count: int
    milestone_count: int
    requires_confirmation: bool = True
    commit_allowed: bool = False
    originals_changed: bool = False

    def as_dict(self) -> dict:
        """Return a JSON-ready preview without provider or source contents."""
        return asdict(self)


_REF = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_WBS_CODE = re.compile(r"[1-9][0-9]*(?:\.[1-9][0-9]*)*")
_DEPENDENCY = re.compile(
    r"(?P<ref>[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_.-]{0,63}?)"
    r"(?P<link>FS|SS|FF|SF)?(?P<lag>[+-][0-9]+[dд])?",
    re.IGNORECASE,
)
_LINK_TYPES = frozenset({"FS", "SS", "FF", "SF"})
_KINDS = {
    "summary": "summary", "раздел": "summary", "этап": "summary", "группа": "summary",
    "work": "work", "работа": "work", "задача": "work",
    "milestone": "milestone", "веха": "milestone", "контрольная точка": "milestone",
}
_HEADERS = {
    "wbs_code": {"wbs", "код wbs", "код", "номер wbs", "шифр"},
    "client_ref": {"client ref", "client_ref", "ссылка строки", "идентификатор строки"},
    "parent_ref": {"parent", "parent ref", "parent_ref", "родитель", "родительский код"},
    "title": {"наименование", "работа", "название", "этап", "описание"},
    "kind": {"тип", "тип строки", "вид"},
    "duration_days": {"длительность", "длительность дней", "duration", "duration days"},
    "dependencies": {"предшественники", "зависимости", "predecessors", "dependencies"},
    "order": {"порядок", "сортировка", "order"},
}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _normalized_header(value: object) -> str:
    return re.sub(r"[^0-9a-zа-яё_]+", " ", _text(value).casefold()).strip()


def _header_field(value: object) -> str | None:
    normalized = _normalized_header(value)
    return next((field for field, aliases in _HEADERS.items() if normalized in aliases), None)


def _positive_row(value: object, fallback: int) -> int:
    if value is None:
        return fallback
    if type(value) is not int or value < 1:
        raise WbsImportError("invalid_source_row", (fallback,))
    return value


def _integer(value: object, *, code: str, source_row: int, minimum: int, maximum: int) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        result = int(value)
    else:
        raise WbsImportError(code, (source_row,))
    if not minimum <= result <= maximum:
        raise WbsImportError(code, (source_row,))
    return result


def _parse_dependency_token(token: str, source_row: int) -> tuple[str, str, int]:
    match = _DEPENDENCY.fullmatch(token.strip())
    if not match:
        raise WbsImportError("invalid_dependency_syntax", (source_row,))
    raw_ref = match.group("ref")
    link = (match.group("link") or "FS").upper()
    lag_token = match.group("lag")
    lag = int(lag_token[:-1]) if lag_token else 0
    if link not in _LINK_TYPES or not -10000 <= lag <= 10000:
        raise WbsImportError("invalid_dependency_syntax", (source_row,))
    return raw_ref, link, lag


def _dependency_values(value: object, source_row: int) -> list[tuple[str, str, int]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        if len(value) > 2000:
            raise WbsImportError("dependency_limit", (source_row,))
        return [_parse_dependency_token(token, source_row) for token in re.split(r"[,;]", value) if token.strip()]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise WbsImportError("invalid_dependency_syntax", (source_row,))
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise WbsImportError("invalid_dependency_syntax", (source_row,))
        reference = _text(item.get("predecessor_ref"))
        link = _text(item.get("link_type") or "FS").upper()
        lag = _integer(item.get("lag_days", 0), code="invalid_dependency_syntax", source_row=source_row,
                       minimum=-10000, maximum=10000)
        if not reference or link not in _LINK_TYPES:
            raise WbsImportError("invalid_dependency_syntax", (source_row,))
        result.append((reference, link, lag))
    return result


def _cycle_nodes(edges: Mapping[str, Iterable[str]], row_by_ref: Mapping[str, int], code: str) -> None:
    state: dict[str, int] = {}

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            start = trail.index(node) if node in trail else 0
            raise WbsImportError(code, (row_by_ref[ref] for ref in trail[start:] + (node,)))
        state[node] = 1
        for target in edges.get(node, ()):
            visit(target, trail + (node,))
        state[node] = 2

    for ref in row_by_ref:
        visit(ref, ())


def normalize_wbs_rows(rows: Iterable[Mapping[str, object]], *, max_depth: int = 5,
                       max_rows: int = 500) -> WbsPreview:
    """Validate and normalize one complete proposed WBS without side effects."""
    supplied = list(rows)
    if not supplied:
        raise WbsImportError("empty_wbs")
    if not 1 <= max_depth <= 20 or not 1 <= max_rows <= 5000 or len(supplied) > max_rows:
        raise WbsImportError("wbs_limit")

    prepared: list[dict] = []
    refs: dict[str, int] = {}
    code_to_ref: dict[str, str] = {}
    source_rows: set[int] = set()
    for index, item in enumerate(supplied, start=1):
        if not isinstance(item, Mapping):
            raise WbsImportError("invalid_row", (index,))
        source_row = _positive_row(item.get("source_row"), index)
        if source_row in source_rows:
            raise WbsImportError("duplicate_source_row", (source_row,))
        source_rows.add(source_row)
        title = _text(item.get("title"))
        if not 2 <= len(title) <= 500:
            raise WbsImportError("invalid_title", (source_row,))
        code = _text(item.get("wbs_code")) or None
        if code is not None and (not _WBS_CODE.fullmatch(code) or len(code.split(".")) > max_depth):
            raise WbsImportError("invalid_wbs_code", (source_row,))
        client_ref = _text(item.get("client_ref")) or (f"wbs_{code.replace('.', '_')}" if code else "")
        if not _REF.fullmatch(client_ref):
            raise WbsImportError("invalid_client_ref", (source_row,))
        if client_ref in refs:
            raise WbsImportError("duplicate_client_ref", (refs[client_ref], source_row))
        if code and code in code_to_ref:
            raise WbsImportError("duplicate_wbs_code", (refs[code_to_ref[code]], source_row))
        refs[client_ref] = source_row
        if code:
            code_to_ref[code] = client_ref
        raw_kind = _text(item.get("kind")).casefold()
        if raw_kind and raw_kind not in _KINDS:
            raise WbsImportError("invalid_wbs_kind", (source_row,))
        raw_order = item.get("order")
        sibling_order = (source_row if raw_order is None or raw_order == "" else
                         _integer(raw_order, code="invalid_wbs_order", source_row=source_row,
                                  minimum=0, maximum=1_000_000))
        prepared.append({
            "source_row": source_row, "client_ref": client_ref, "wbs_code": code,
            "raw_parent": _text(item.get("parent_ref")) or None,
            "explicit_kind": _KINDS.get(raw_kind), "title": title,
            "duration_value": item.get("duration_days"), "dependency_value": item.get("dependencies"),
            "sibling_order": sibling_order,
        })

    by_ref = {item["client_ref"]: item for item in prepared}

    def resolve_reference(raw: str, source_row: int, missing_code: str) -> str:
        resolved = code_to_ref.get(raw, raw)
        if resolved not in by_ref:
            raise WbsImportError(missing_code, (source_row,))
        return resolved

    parent_edges: dict[str, tuple[str, ...]] = {}
    children: dict[str | None, list[str]] = defaultdict(list)
    for item in prepared:
        inferred = None
        code = item["wbs_code"]
        if code and "." in code:
            inferred_code = code.rsplit(".", 1)[0]
            inferred = code_to_ref.get(inferred_code)
            if inferred is None:
                raise WbsImportError("missing_parent", (item["source_row"],))
        explicit = (resolve_reference(item["raw_parent"], item["source_row"], "missing_parent")
                    if item["raw_parent"] else None)
        if explicit and inferred and explicit != inferred:
            raise WbsImportError("parent_conflict", (item["source_row"],))
        parent = explicit or inferred
        if parent == item["client_ref"]:
            raise WbsImportError("parent_cycle", (item["source_row"],))
        item["parent_ref"] = parent
        parent_edges[item["client_ref"]] = (parent,) if parent else ()
        children[parent].append(item["client_ref"])
    _cycle_nodes(parent_edges, refs, "parent_cycle")

    depth_cache: dict[str, int] = {}

    def depth(ref: str) -> int:
        if ref not in depth_cache:
            parent = by_ref[ref]["parent_ref"]
            depth_cache[ref] = 1 if parent is None else depth(parent) + 1
        value = depth_cache[ref]
        if value > max_depth:
            raise WbsImportError("wbs_depth_limit", (by_ref[ref]["source_row"],))
        return value

    dependency_edges: dict[str, list[str]] = defaultdict(list)
    for item in prepared:
        ref = item["client_ref"]
        has_children = bool(children.get(ref))
        explicit_kind = item["explicit_kind"]
        if has_children and explicit_kind in {"work", "milestone"}:
            raise WbsImportError("parent_must_be_summary", (item["source_row"],))
        kind = "summary" if has_children else (explicit_kind or "work")
        if kind == "summary":
            if item["duration_value"] not in (None, "") or item["dependency_value"] not in (None, "", (), []):
                raise WbsImportError("summary_has_planning_fields", (item["source_row"],))
            duration = None
            dependencies = ()
        else:
            default_duration = 0 if kind == "milestone" else None
            raw_duration = item["duration_value"] if item["duration_value"] not in (None, "") else default_duration
            if raw_duration is None:
                raise WbsImportError("duration_required", (item["source_row"],))
            duration = _integer(raw_duration, code="invalid_duration", source_row=item["source_row"],
                                minimum=0 if kind == "milestone" else 1,
                                maximum=0 if kind == "milestone" else 10000)
            normalized_dependencies = []
            seen = set()
            for raw_ref, link, lag in _dependency_values(item["dependency_value"], item["source_row"]):
                predecessor = resolve_reference(raw_ref, item["source_row"], "missing_dependency")
                if predecessor == ref:
                    raise WbsImportError("self_dependency", (item["source_row"],))
                if predecessor in seen:
                    raise WbsImportError("duplicate_dependency", (item["source_row"],))
                seen.add(predecessor)
                normalized_dependencies.append(WbsDependency(predecessor, link, lag))
                dependency_edges[ref].append(predecessor)
            dependencies = tuple(sorted(normalized_dependencies, key=lambda value: value.predecessor_ref))
        item.update(kind=kind, duration_days=duration, dependencies=dependencies, depth=depth(ref))

    for item in prepared:
        if any(by_ref[dependency.predecessor_ref]["kind"] == "summary" for dependency in item["dependencies"]):
            raise WbsImportError("summary_dependency_forbidden", (item["source_row"],))
    _cycle_nodes(dependency_edges, refs, "dependency_cycle")

    for sibling_refs in children.values():
        sibling_refs.sort(key=lambda ref: (by_ref[ref]["sibling_order"], by_ref[ref]["source_row"], ref))
    ordered_refs: list[str] = []

    def append_tree(ref: str) -> None:
        ordered_refs.append(ref)
        for child in children.get(ref, ()):
            append_tree(child)

    for root in children.get(None, ()):
        append_tree(root)
    if len(ordered_refs) != len(prepared):
        raise WbsImportError("parent_cycle")

    normalized = tuple(WbsPreviewRow(
        source_row=by_ref[ref]["source_row"], client_ref=ref, wbs_code=by_ref[ref]["wbs_code"],
        parent_ref=by_ref[ref]["parent_ref"], depth=by_ref[ref]["depth"], order=order,
        sibling_order=by_ref[ref]["sibling_order"], kind=by_ref[ref]["kind"],
        title=by_ref[ref]["title"], duration_days=by_ref[ref]["duration_days"],
        dependencies=by_ref[ref]["dependencies"],
    ) for order, ref in enumerate(ordered_refs))
    return WbsPreview(
        rows=normalized, max_depth=max(row.depth for row in normalized),
        summary_count=sum(row.kind == "summary" for row in normalized),
        work_count=sum(row.kind == "work" for row in normalized),
        milestone_count=sum(row.kind == "milestone" for row in normalized),
    )


def parse_wbs_csv(content: str, *, max_depth: int = 5, limit: int = 500) -> WbsPreview:
    """Parse a bounded CSV/TSV WBS into the same side-effect-free preview."""
    if not isinstance(content, str) or not content.strip() or len(content) > 5_000_000:
        raise WbsImportError("invalid_source")
    sample = content[:20_000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        reader = csv.reader(io.StringIO(content), dialect)
    except csv.Error:
        counts = {delimiter: sample.count(delimiter) for delimiter in (",", ";", "\t", "|")}
        delimiter = max(counts, key=counts.get)
        if counts[delimiter] == 0:
            raise WbsImportError("required_headers_missing")
        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    matrix = [row for row in reader if any(cell.strip() for cell in row)]
    if not matrix:
        raise WbsImportError("empty_wbs")
    best_index = -1
    best_mapping: dict[int, str] = {}
    for index, row in enumerate(matrix[:20]):
        mapping: dict[int, str] = {}
        for column, cell in enumerate(row):
            field = _header_field(cell)
            if field:
                if field in mapping.values():
                    raise WbsImportError("duplicate_header", (index + 1,))
                mapping[column] = field
        if len(mapping) > len(best_mapping):
            best_index, best_mapping = index, mapping
    if "title" not in best_mapping.values() or not ({"wbs_code", "client_ref"} & set(best_mapping.values())):
        raise WbsImportError("required_headers_missing", (best_index + 1,) if best_index >= 0 else ())
    data = matrix[best_index + 1:]
    if len(data) > limit:
        raise WbsImportError("wbs_limit")
    rows = []
    for source_row, cells in enumerate(data, start=best_index + 2):
        row = {field: cells[column].strip() for column, field in best_mapping.items() if column < len(cells)}
        row["source_row"] = source_row
        rows.append(row)
    return normalize_wbs_rows(rows, max_depth=max_depth, max_rows=limit)
