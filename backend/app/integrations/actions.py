from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.orm import Session

from app.google_calendar import sync_tasks_to_calendar
from app.google_tasks import sync_tasks_to_google
from app.integrations.contracts import ActionAdapter, AdapterHealth
from app.integrations.google_workspace import google_workspace_for_project
from app.models.task import Task


@dataclass(frozen=True, slots=True)
class ActionSyncResult:
    task_synced: int = 0
    task_failed: int = 0
    calendar_synced: int = 0
    calendar_failed: int = 0


class GoogleWorkspaceActionAdapter:
    """Current Tasks/Calendar implementation behind the provider-neutral boundary."""

    provider = "google_workspace"

    def __init__(self, project_id: int, db: Session):
        self.project_id = project_id
        self.db = db

    def health(self) -> AdapterHealth:
        return google_workspace_for_project(self.project_id, self.db).health()

    def sync_tasks(self, tasks: Sequence[Task], force_update: bool = False) -> tuple[int, int]:
        return sync_tasks_to_google(self.db, self.project_id, list(tasks), force_update=force_update)

    def sync_calendar(self, tasks: Sequence[Task], force_update: bool = False) -> tuple[int, int]:
        return sync_tasks_to_calendar(self.db, self.project_id, list(tasks), force_update=force_update)


def configured_action_adapter(project_id: int, db: Session) -> ActionAdapter:
    # Vertical Slice 1 intentionally has one implementation. Core callers depend
    # only on ActionAdapter, so adding another provider does not change workflows.
    return GoogleWorkspaceActionAdapter(project_id, db)


def publish_actions(
    adapter: ActionAdapter,
    tasks: Sequence[Task],
    *,
    publish_tasks: bool = True,
    publish_calendar: bool = True,
    force_update: bool = False,
) -> ActionSyncResult:
    task_synced = task_failed = calendar_synced = calendar_failed = 0
    if publish_tasks:
        task_synced, task_failed = adapter.sync_tasks(tasks, force_update=force_update)
    if publish_calendar:
        calendar_synced, calendar_failed = adapter.sync_calendar(tasks, force_update=force_update)
    return ActionSyncResult(task_synced, task_failed, calendar_synced, calendar_failed)
