from __future__ import annotations

from dataclasses import dataclass, field
from typing import BinaryIO, Collection, Iterator, Protocol, runtime_checkable


class StagingError(RuntimeError):
    """Stable, content-free staging boundary error."""


class StagingIntegrityError(StagingError):
    pass


class StagingSecurityError(StagingError):
    pass


class StagingConflict(StagingError):
    pass


class StagingLimitExceeded(StagingError):
    pass


class StagingIOError(StagingError):
    pass


@dataclass(frozen=True, slots=True)
class KekRef:
    reference: str
    version: str


@runtime_checkable
class KekResolver(Protocol):
    def resolve(self, reference: str, version: str) -> bytes:
        """Return exactly one named/versioned 256-bit KEK or fail closed."""
        ...


@dataclass(frozen=True, slots=True)
class StagingDescriptor:
    """Non-business metadata safe to persist outside the ciphertext.

    It intentionally has no filename, MIME, locator, owner, project, URL,
    filesystem path, plaintext digest or plaintext size.
    """

    object_id: str
    format_version: int
    chunk_size: int
    kek: KekRef
    wrapped_dek: str = field(repr=False)


@runtime_checkable
class StagingStorage(Protocol):
    def write(
        self,
        object_id: str,
        source: BinaryIO,
        *,
        max_bytes: int,
        kek: KekRef,
        fence: str,
    ) -> StagingDescriptor: ...

    def read_chunks(self, descriptor: StagingDescriptor, *, max_bytes: int) -> Iterator[bytes]:
        """Yield bounded authenticated chunks.

        A caller must exhaust the iterator before committing derived effects:
        only exhaustion authenticates the footer, total size and digest.
        """
        ...

    def delete(self, object_id: str) -> None: ...

    def cleanup_partials(
        self,
        object_id: str,
        *,
        eligible_fences: Collection[str],
        active_fences: Collection[str],
    ) -> int: ...
