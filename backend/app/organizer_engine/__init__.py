"""Public organizer API with lazy imports.

Keeping the pure planning and Drive safety modules independent from the
database layer makes them usable in workers and straightforward to test.
"""

__all__ = [
    "DriveClient",
    "UnsafeDriveMutation",
    "CopyLimitExceeded",
    "OrganizerExecutor",
    "build_proposal",
    "OrganizerRepository",
]


def __getattr__(name):
    if name in {"DriveClient", "UnsafeDriveMutation", "CopyLimitExceeded"}:
        from .drive import CopyLimitExceeded, DriveClient, UnsafeDriveMutation
        return locals()[name]
    if name == "OrganizerExecutor":
        from .executor import OrganizerExecutor
        return OrganizerExecutor
    if name == "build_proposal":
        from .planner import build_proposal
        return build_proposal
    if name == "OrganizerRepository":
        from .repository import OrganizerRepository
        return OrganizerRepository
    raise AttributeError(name)
