"""Isolated encrypted staging primitives; importing has no side effects."""

from app.staging.contracts import (
    KekRef,
    KekResolver,
    StagingConflict,
    StagingDescriptor,
    StagingError,
    StagingIntegrityError,
    StagingSecurityError,
    StagingStorage,
)
from app.staging.filesystem import FilesystemStagingStorage, new_fence, new_object_id

__all__ = [
    "FilesystemStagingStorage",
    "KekRef",
    "KekResolver",
    "StagingConflict",
    "StagingDescriptor",
    "StagingError",
    "StagingIntegrityError",
    "StagingSecurityError",
    "StagingStorage",
    "new_fence",
    "new_object_id",
]
