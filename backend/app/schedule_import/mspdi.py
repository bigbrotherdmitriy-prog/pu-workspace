from __future__ import annotations

from datetime import date
import re
from xml.etree.ElementTree import Element, SubElement, tostring


NS = "http://schemas.microsoft.com/project"


def _text(parent: Element, name: str, value: object) -> None:
    SubElement(parent, f"{{{NS}}}{name}").text = str(value)


def _timestamp(value: date | None) -> str | None:
    return f"{value.isoformat()}T08:00:00" if value else None


def build_mspdi(project_name: str, tasks: list[dict]) -> bytes:
    """Build a Microsoft Project XML (MSPDI) export from native GPR rows."""
    root = Element(f"{{{NS}}}Project")
    _text(root, "SaveVersion", 14)
    _text(root, "Name", project_name)
    _text(root, "ScheduleFromStart", 1)
    task_root = SubElement(root, f"{{{NS}}}Tasks")
    uid_by_id = {int(row["id"]): index + 1 for index, row in enumerate(tasks)}
    level_by_id: dict[int, int] = {}
    for index, row in enumerate(tasks):
        parent_id = row.get("parent_id")
        level_by_id[int(row["id"])] = level_by_id.get(int(parent_id), 0) + 1 if parent_id else 1
        node = SubElement(task_root, f"{{{NS}}}Task")
        uid = uid_by_id[int(row["id"])]
        _text(node, "UID", uid); _text(node, "ID", uid); _text(node, "Name", row["title"])
        _text(node, "OutlineLevel", level_by_id[int(row["id"])]); _text(node, "Milestone", int(bool(row.get("is_milestone"))))
        if start := _timestamp(row.get("planned_start")): _text(node, "Start", start)
        if finish := _timestamp(row.get("planned_finish")): _text(node, "Finish", finish)
        _text(node, "Duration", f"PT{max(0, int(row.get('duration_days') or 0)) * 8}H0M0S")
        _text(node, "PercentComplete", round(float(row.get("actual_progress") or 0)))
        for token in str(row.get("predecessor_ids") or "").replace(";", ",").split(","):
            match = re.match(r"\s*(\d+)", token)
            predecessor_uid = uid_by_id.get(int(match.group(1))) if match else None
            if predecessor_uid:
                link = SubElement(node, f"{{{NS}}}PredecessorLink")
                _text(link, "PredecessorUID", predecessor_uid); _text(link, "Type", 1)
    return tostring(root, encoding="utf-8", xml_declaration=True)
