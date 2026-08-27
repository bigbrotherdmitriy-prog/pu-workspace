from __future__ import annotations

from collections import Counter

from .config import FOLDER_STRUCTURE, MIN_AUTO_CONFIDENCE
from .drive import DriveClient
from .repository import OrganizerRepository


def source_metadata_changed(item, current) -> bool:
    if current.name != item["source"] or current.parent_id != item["current_parent_id"]:
        return True
    expected_modified = item.get("source_modified_at")
    expected_checksum = item.get("source_checksum")
    return bool(
        (expected_modified and current.modified_time != expected_modified)
        or (expected_checksum and current.md5_checksum != expected_checksum)
    )


class OrganizerExecutor:
    def __init__(self, repo: OrganizerRepository, drive: DriveClient):
        self.repo = repo
        self.drive = drive

    def _target_folders(self, copy_root_id: str) -> dict[str, str]:
        existing = {
            x.name: x.id
            for x in self.drive.list_children(copy_root_id)
            if x.is_folder
        }

        result: dict[str, str] = {}

        for name, _ in FOLDER_STRUCTURE:
            result[name] = existing.get(name) or self.drive.create_folder(
                name,
                copy_root_id,
            )

        return result

    def _approved_items(self, proposal_id: int):
        return [
            item
            for item in self.repo.proposal_items(proposal_id)
            if item["user_decision"] in {"approved", "edited"}
        ]

    def _preflight(self, proposal_id: int) -> None:
        """
        Fail CLOSED before touching Drive.

        A bulk-approved proposal may move safe files, but:
        - low-confidence items are blocked unless explicitly edited;
        - duplicate/version/ambiguous/collision items are blocked unless edited;
        - duplicate target names are rejected.
        """
        items = self._approved_items(proposal_id)

        unsafe = []

        for item in items:
            explicitly_edited = item["user_decision"] == "edited"

            if explicitly_edited:
                continue

            special = item["special_case"]

            if special in {
                "duplicate",
                "version",
                "ambiguous",
                "name_collision",
            }:
                unsafe.append(
                    f'{item["file_id"]}: special_case={special}'
                )
                continue

            if float(item["confidence"] or 0) < MIN_AUTO_CONFIDENCE:
                unsafe.append(
                    f'{item["file_id"]}: confidence={item["confidence"]}'
                )

        if unsafe:
            preview = "; ".join(unsafe[:20])
            more = max(0, len(unsafe) - 20)

            raise ValueError(
                "Safety preflight blocked apply: "
                f"{len(unsafe)} approved actions require explicit review/edit. "
                f"{preview}"
                + (f"; ... and {more} more" if more else "")
            )

        targets = []

        for item in items:
            folder = item["edited_folder"] or item["target_folder"]
            name = item["edited_name"] or item["proposed_name"]
            targets.append((folder, name))

        collisions = [
            (folder, name, count)
            for (folder, name), count in Counter(targets).items()
            if count > 1
        ]

        if collisions:
            preview = "; ".join(
                f"{folder}/{name} x{count}"
                for folder, name, count in collisions[:20]
            )

            raise ValueError(
                "Safety preflight blocked apply: "
                f"{len(collisions)} target-name collision groups detected. "
                f"{preview}"
            )

    def _recheck_sources(self, proposal_id: int) -> None:
        conflicts: list[int] = []
        for item in self._approved_items(proposal_id):
            current = self.drive.get_file_meta(item["file_id"])
            if source_metadata_changed(item, current):
                conflicts.append(int(item["id"]))
        if conflicts:
            self.repo.mark_source_conflicts(proposal_id, conflicts)
            raise ValueError(
                "Safety dry-run blocked apply: source metadata changed after analysis "
                f"for {len(conflicts)} action(s). Re-scan is required."
            )

    def apply(self, proposal_id: int) -> dict[str, int]:
        proposal = self.repo.proposal(proposal_id)

        if not proposal:
            raise ValueError("Proposal not found")

        if proposal["status"] == "applied":
            return {"renamed": 0, "moved": 0, "skipped": 0, "errors": 0, "already_applied": 1}

        if proposal["status"] != "ready_to_apply_to_copy":
            raise ValueError("Proposal is not prepared for apply")

        copy_root = proposal["copy_folder_id"]

        if not copy_root:
            raise ValueError("Safe copy is missing")

        # Absolutely no Drive mutation before this passes.
        self._preflight(proposal_id)
        self._recheck_sources(proposal_id)

        session_id = int(proposal["session_id"])
        folders = self._target_folders(copy_root)

        stats = {
            "renamed": 0,
            "moved": 0,
            "skipped": 0,
            "errors": 0,
            "already_applied": 0,
        }

        applied: list[tuple[str, str, dict, dict]] = []

        try:
            for item in self.repo.proposal_items(proposal_id):
                if item["user_decision"] not in {"approved", "edited"}:
                    stats["skipped"] += 1
                    continue

                target_folder = (
                    item["edited_folder"] or item["target_folder"]
                )
                target_name = (
                    item["edited_name"] or item["proposed_name"]
                )

                target_parent = folders.get(target_folder)

                if not target_parent:
                    raise ValueError(
                        f"Unknown target folder: {target_folder}"
                    )

                file_id = item["file_id"]

                # Hard safety boundary:
                # rejects original/outside-copy IDs.
                self.drive.assert_inside_copy(file_id, copy_root)

                current = self.drive.get_file_meta(file_id)

                if current.name != target_name:
                    before = {
                        "name": current.name,
                        "parent_id": current.parent_id,
                    }
                    after = {
                        "name": target_name,
                        "parent_id": current.parent_id,
                    }

                    self.drive.rename_file(
                        file_id,
                        target_name,
                        copy_root,
                    )

                    self.repo.log_operation(
                        proposal_id,
                        session_id,
                        file_id,
                        "rename",
                        before,
                        after,
                    )

                    applied.append(
                        ("rename", file_id, before, after)
                    )

                    stats["renamed"] += 1
                    current = self.drive.get_file_meta(file_id)

                if current.parent_id != target_parent:
                    before = {
                        "name": current.name,
                        "parent_id": current.parent_id,
                    }
                    after = {
                        "name": current.name,
                        "parent_id": target_parent,
                    }

                    self.drive.move_file(
                        file_id,
                        target_parent,
                        current.parent_id,
                        copy_root,
                    )

                    self.repo.log_operation(
                        proposal_id,
                        session_id,
                        file_id,
                        "move",
                        before,
                        after,
                    )

                    applied.append(
                        ("move", file_id, before, after)
                    )

                    stats["moved"] += 1

            self.repo.db.commit()
            self.repo.mark_applied(proposal_id)
            return stats

        except Exception:
            # Google Drive has no cross-request transaction.
            # Compensate in reverse order.
            stats["errors"] += 1

            for op_type, file_id, before, after in reversed(applied):
                try:
                    if op_type == "rename":
                        self.drive.rename_file(
                            file_id,
                            before["name"],
                            copy_root,
                        )

                    elif op_type == "move":
                        self.drive.move_file(
                            file_id,
                            before["parent_id"],
                            after["parent_id"],
                            copy_root,
                        )

                except Exception:
                    # Preserve original exception.
                    pass

            self.repo.db.rollback()
            raise

    def apply_one_to_source(self, proposal_id: int, action_id: int) -> dict[str, int]:
        """Apply one explicitly selected rename to the original source, fail closed."""
        proposal = self.repo.proposal(proposal_id)
        if not proposal or not str(proposal["copy_folder_id"]).startswith("virtual:"):
            raise ValueError("Source apply requires a virtual snapshot proposal")
        if proposal["status"] not in {"approved", "waiting_confirmation"}:
            if proposal["status"] == "applied":
                return {"renamed": 0, "already_applied": 1}
            raise ValueError("Proposal is not available for source apply")
        item = next((row for row in self.repo.proposal_items(proposal_id) if int(row["id"]) == action_id), None)
        if item is None or item["user_decision"] not in {"approved", "edited"}:
            raise ValueError("The selected action must be explicitly approved or edited")
        if item["user_decision"] != "edited" and (item["special_case"] or float(item["confidence"] or 0) < MIN_AUTO_CONFIDENCE):
            raise ValueError("Low-confidence or special-case actions must be explicitly edited")
        if any(op["file_id"] == item["file_id"] and op["op_type"] == "source_rename" for op in self.repo.operations(proposal_id)):
            return {"renamed": 0, "already_applied": 1}
        current = self.drive.get_file_meta(item["file_id"])
        if source_metadata_changed(item, current):
            self.repo.mark_source_conflicts(proposal_id, [int(item["id"])])
            raise ValueError("Safety dry-run blocked apply: source metadata changed after snapshot")
        target_name = item["edited_name"] or item["proposed_name"]
        if current.name == target_name:
            return {"renamed": 0, "already_applied": 1}
        siblings = self.drive.list_children(current.parent_id)
        if any(row.id != current.id and row.name == target_name for row in siblings):
            raise ValueError("Target name already exists; source file was not changed")
        source_root = proposal["source_folder_id"]
        self.drive.assert_inside_copy(current.id, source_root)
        before = {"name": current.name, "parent_id": current.parent_id}
        after = {"name": target_name, "parent_id": current.parent_id}
        self.drive.rename_file(current.id, target_name, source_root)
        self.repo.log_operation(proposal_id, int(proposal["session_id"]), current.id, "source_rename", before, after)
        self.repo.db.commit()
        self.repo.mark_source_applied(proposal_id)
        return {"renamed": 1, "already_applied": 0}

    def rollback(
        self,
        proposal_id: int,
        limit: int = 500,
    ) -> dict[str, int]:
        proposal = self.repo.proposal(proposal_id)

        if not proposal or not proposal["copy_folder_id"]:
            raise ValueError("Proposal/safe copy not found")

        if proposal["status"] not in {"applied", "rollback_partial"}:
            raise ValueError("Only an applied proposal can be rolled back")

        copy_root = proposal["source_folder_id"] if proposal["originals_modified"] else proposal["copy_folder_id"]

        stats = {
            "rolled_back": 0,
            "skipped": 0,
            "errors": 0,
        }

        for op in self.repo.operations(proposal_id, limit):
            if op["rolled_back_at"] is not None:
                stats["skipped"] += 1
                continue

            try:
                self.drive.assert_inside_copy(
                    op["file_id"],
                    copy_root,
                )

                before = op["before_json"]
                after = op["after_json"]

                if op["op_type"] in {"rename", "source_rename"}:
                    self.drive.rename_file(
                        op["file_id"],
                        before["name"],
                        copy_root,
                    )

                elif op["op_type"] == "move":
                    self.drive.move_file(
                        op["file_id"],
                        before["parent_id"],
                        after["parent_id"],
                        copy_root,
                    )

                else:
                    stats["skipped"] += 1
                    continue

                self.repo.mark_rolled_back(op["id"])
                stats["rolled_back"] += 1

            except Exception:
                stats["errors"] += 1

        self.repo.mark_rollback_result(proposal_id, complete=stats["errors"] == 0)
        return stats
