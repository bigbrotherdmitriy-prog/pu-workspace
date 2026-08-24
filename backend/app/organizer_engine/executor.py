from __future__ import annotations

from collections import Counter

from .config import FOLDER_STRUCTURE, MIN_AUTO_CONFIDENCE
from .drive import DriveClient
from .repository import OrganizerRepository


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

    def apply(self, proposal_id: int) -> dict[str, int]:
        proposal = self.repo.proposal(proposal_id)

        if not proposal:
            raise ValueError("Proposal not found")

        if proposal["status"] not in {
            "approved",
            "ready_to_apply_to_copy",
        }:
            raise ValueError("Proposal must be approved before apply")

        copy_root = proposal["copy_folder_id"]

        if not copy_root:
            raise ValueError("Safe copy is missing")

        # Absolutely no Drive mutation before this passes.
        self._preflight(proposal_id)

        session_id = int(proposal["session_id"])
        folders = self._target_folders(copy_root)

        stats = {
            "renamed": 0,
            "moved": 0,
            "skipped": 0,
            "errors": 0,
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

    def rollback(
        self,
        proposal_id: int,
        limit: int = 500,
    ) -> dict[str, int]:
        proposal = self.repo.proposal(proposal_id)

        if not proposal or not proposal["copy_folder_id"]:
            raise ValueError("Proposal/safe copy not found")

        copy_root = proposal["copy_folder_id"]

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

                if op["op_type"] == "rename":
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

        return stats
