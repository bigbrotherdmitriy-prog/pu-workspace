from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import struct
from pathlib import Path
from typing import BinaryIO, Collection, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.staging.contracts import (
    KekRef,
    KekResolver,
    StagingConflict,
    StagingDescriptor,
    StagingError,
    StagingIOError,
    StagingIntegrityError,
    StagingLimitExceeded,
    StagingSecurityError,
)
from app.staging.crypto import NONCE_BYTES, unwrap_dek, validate_kek_ref, wrap_dek

MAGIC = b"PUWSTG2\x00"
FORMAT_VERSION = 2
DEFAULT_CHUNK_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 8 * 1024 * 1024
MAX_OBJECT_BYTES = 1 << 40
MAX_FOOTER_CIPHERTEXT = 512
MAX_CLEANUP_FENCES = 1024
TAG_BYTES = 16
ID_RE = re.compile(r"^[0-9a-f]{32}$")

HEADER = struct.Struct(">8sBI16s")
CHUNK_HEADER = struct.Struct(">BQII12s")
FOOTER_HEADER = struct.Struct(">BQI12s")
CHUNK_MARKER = 1
FOOTER_MARKER = 2
CHUNK_DOMAIN = b"PUW-STAGING-CHUNK-V2\x00"
FOOTER_DOMAIN = b"PUW-STAGING-FOOTER-V2\x00"


def new_object_id() -> str:
    return secrets.token_hex(16)


def new_fence() -> str:
    return secrets.token_hex(16)


def _valid_id(value: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise StagingSecurityError("invalid_opaque_id")
    return value


def _valid_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_OBJECT_BYTES:
        raise StagingSecurityError("invalid_size_limit")
    return value


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _read_exact(source, length: int) -> bytes:
    if length < 0 or length > MAX_CHUNK_BYTES + TAG_BYTES:
        raise StagingIntegrityError("invalid_frame_length")
    value = bytearray(length)
    offset = 0
    remaining = length
    while remaining:
        chunk = source.read(remaining)
        if not isinstance(chunk, bytes) or not chunk:
            raise StagingIntegrityError("truncated_ciphertext")
        if len(chunk) > remaining:
            raise StagingIntegrityError("invalid_stream_read")
        value[offset:offset + len(chunk)] = chunk
        offset += len(chunk)
        remaining -= len(chunk)
    return bytes(value)


def _chunk_aad(header: bytes, object_id: str, index: int, plaintext_length: int) -> bytes:
    return (CHUNK_DOMAIN + header + object_id.encode("ascii")
            + struct.pack(">QI", index, plaintext_length))


def _footer_aad(header: bytes, object_id: str, chunk_count: int) -> bytes:
    return FOOTER_DOMAIN + header + object_id.encode("ascii") + struct.pack(">Q", chunk_count)


class FilesystemStagingStorage:
    """Private, ciphertext-only filesystem store with atomic no-overwrite publish."""

    def __init__(self, root: str | Path, kek_resolver: KekResolver, *, chunk_size=DEFAULT_CHUNK_BYTES):
        if not isinstance(root, (str, os.PathLike)) or not os.fspath(root):
            raise StagingSecurityError("invalid_storage_root")
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or not 1 <= chunk_size <= MAX_CHUNK_BYTES:
            raise StagingSecurityError("invalid_chunk_size")
        if not isinstance(kek_resolver, KekResolver):
            raise StagingSecurityError("invalid_key_resolver")
        self._root = Path(os.path.abspath(os.fspath(root)))
        self._resolver = kek_resolver
        self._chunk_size = chunk_size
        self._prepare_root()

    def __repr__(self):
        return "<FilesystemStagingStorage private>"

    def _prepare_root(self):
        current = self._root
        while True:
            if _is_linklike(current):
                raise StagingSecurityError("unsafe_storage_root")
            if current == current.parent:
                break
            current = current.parent
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if _is_linklike(self._root) or not self._root.is_dir():
                raise StagingSecurityError("unsafe_storage_root")
            os.chmod(self._root, 0o700)
            if os.name == "posix" and stat.S_IMODE(self._root.stat().st_mode) != 0o700:
                raise StagingSecurityError("unsafe_storage_permissions")
        except StagingError:
            raise
        except OSError:
            raise StagingIOError("storage_root_unavailable") from None

    def _check_root(self):
        try:
            value = self._root.lstat()
            if _is_linklike(self._root) or stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                raise StagingSecurityError("unsafe_storage_root")
            if os.name == "posix" and stat.S_IMODE(value.st_mode) & 0o077:
                raise StagingSecurityError("unsafe_storage_permissions")
        except StagingError:
            raise
        except OSError:
            raise StagingIOError("storage_root_unavailable") from None

    def _shard(self, object_id: str, *, create: bool) -> Path:
        self._check_root()
        shard = self._root / object_id[:2]
        try:
            if _is_linklike(shard):
                raise StagingSecurityError("unsafe_storage_shard")
            if create:
                shard.mkdir(mode=0o700, exist_ok=True)
                os.chmod(shard, 0o700)
            value = shard.lstat()
            if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                raise StagingSecurityError("unsafe_storage_shard")
            if os.name == "posix" and stat.S_IMODE(value.st_mode) & 0o077:
                raise StagingSecurityError("unsafe_storage_permissions")
        except FileNotFoundError:
            raise
        except StagingError:
            raise
        except OSError:
            raise StagingIOError("storage_shard_unavailable") from None
        return shard

    def _path(self, object_id: str, *, create_shard=False) -> Path:
        object_id = _valid_id(object_id)
        return self._shard(object_id, create=create_shard) / f"{object_id}.enc"

    def _partial(self, object_id: str, fence: str, *, create_shard=False) -> Path:
        object_id, fence = _valid_id(object_id), _valid_id(fence)
        return self._shard(object_id, create=create_shard) / f"{object_id}.partial.{fence}"

    @staticmethod
    def _safe_regular(path: Path, *, allow_missing=False):
        try:
            value = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return None
            raise StagingIOError("object_unavailable") from None
        except OSError:
            raise StagingIOError("object_unavailable") from None
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise StagingSecurityError("unsafe_storage_object")
        return value

    @staticmethod
    def _fsync_directory(path: Path):
        if os.name != "posix":
            return
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise StagingIOError("directory_sync_failed") from None

    def write(self, object_id: str, source: BinaryIO, *, max_bytes: int, kek: KekRef,
              fence: str) -> StagingDescriptor:
        object_id, fence = _valid_id(object_id), _valid_id(fence)
        max_bytes = _valid_limit(max_bytes)
        validate_kek_ref(kek)
        if not callable(getattr(source, "read", None)):
            raise StagingSecurityError("invalid_plaintext_stream")
        final = self._path(object_id, create_shard=True)
        partial = self._partial(object_id, fence)
        try:
            existing = final.lstat()
        except FileNotFoundError:
            existing = None
        except OSError:
            raise StagingIOError("object_unavailable") from None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
                raise StagingSecurityError("unsafe_storage_object")
            raise StagingConflict("object_exists")
        if self._safe_regular(partial, allow_missing=True) is not None:
            raise StagingConflict("writer_fence_exists")

        dek = bytearray(secrets.token_bytes(32))
        header = HEADER.pack(MAGIC, FORMAT_VERSION, self._chunk_size, bytes.fromhex(object_id))
        digest = hashlib.sha256()
        size = 0
        index = 0
        published = False
        try:
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(partial, flags, 0o600)
                with os.fdopen(descriptor, "wb", buffering=0) as target:
                    if os.name == "posix":
                        os.fchmod(target.fileno(), 0o600)
                    target.write(header)
                    while True:
                        request = min(self._chunk_size, max_bytes - size + 1)
                        if request <= 0:
                            request = 1
                        chunk = source.read(request)
                        if not isinstance(chunk, bytes) or len(chunk) > request:
                            raise StagingIOError("plaintext_stream_failed")
                        if not chunk:
                            break
                        if size + len(chunk) > max_bytes:
                            raise StagingLimitExceeded("object_too_large")
                        digest.update(chunk)
                        nonce = secrets.token_bytes(NONCE_BYTES)
                        ciphertext = AESGCM(bytes(dek)).encrypt(
                            nonce, chunk, _chunk_aad(header, object_id, index, len(chunk)),
                        )
                        target.write(CHUNK_HEADER.pack(
                            CHUNK_MARKER, index, len(chunk), len(ciphertext), nonce,
                        ))
                        target.write(ciphertext)
                        size += len(chunk)
                        index += 1
                    envelope = json.dumps(
                        {"chunks": index, "sha256": digest.hexdigest(), "size": size},
                        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                    ).encode("ascii")
                    nonce = secrets.token_bytes(NONCE_BYTES)
                    encrypted_footer = AESGCM(bytes(dek)).encrypt(
                        nonce, envelope, _footer_aad(header, object_id, index),
                    )
                    if len(encrypted_footer) > MAX_FOOTER_CIPHERTEXT:
                        raise StagingIntegrityError("footer_too_large")
                    target.write(FOOTER_HEADER.pack(FOOTER_MARKER, index, len(encrypted_footer), nonce))
                    target.write(encrypted_footer)
                    target.flush()
                    os.fsync(target.fileno())
                self._safe_regular(partial)
                wrapped = wrap_dek(bytes(dek), object_id=object_id, kek=kek, resolver=self._resolver)
                try:
                    os.link(partial, final, follow_symlinks=False)
                except FileExistsError:
                    try:
                        occupied = final.lstat()
                    except OSError:
                        raise StagingIOError("publish_failed") from None
                    # The winning writer briefly has two names for the same
                    # inode between link() and partial unlink().  That is still
                    # an ordinary no-overwrite conflict, not a hardlink read.
                    if stat.S_ISLNK(occupied.st_mode) or not stat.S_ISREG(occupied.st_mode):
                        raise StagingSecurityError("unsafe_storage_object")
                    raise StagingConflict("object_exists") from None
                except OSError:
                    raise StagingIOError("publish_failed") from None
                partial.unlink()
                published = True
                self._safe_regular(final)
                self._fsync_directory(final.parent)
                return StagingDescriptor(
                    object_id=object_id, format_version=FORMAT_VERSION,
                    chunk_size=self._chunk_size, kek=kek, wrapped_dek=wrapped,
                )
            except StagingError:
                raise
            except OSError:
                raise StagingIOError("write_failed") from None
            except Exception:
                raise StagingIOError("plaintext_stream_failed") from None
        finally:
            for offset in range(len(dek)):
                dek[offset] = 0
            if not published:
                try:
                    if self._safe_regular(partial, allow_missing=True) is not None:
                        partial.unlink()
                except StagingError:
                    raise StagingIOError("partial_cleanup_failed") from None
                except OSError:
                    raise StagingIOError("partial_cleanup_failed") from None

    def read_chunks(self, descriptor: StagingDescriptor, *, max_bytes: int) -> Iterator[bytes]:
        if not isinstance(descriptor, StagingDescriptor):
            raise StagingSecurityError("invalid_descriptor")
        object_id = _valid_id(descriptor.object_id)
        max_bytes = _valid_limit(max_bytes)
        if (descriptor.format_version != FORMAT_VERSION or isinstance(descriptor.chunk_size, bool)
                or not 1 <= descriptor.chunk_size <= MAX_CHUNK_BYTES):
            raise StagingIntegrityError("unsupported_format")
        validate_kek_ref(descriptor.kek)
        dek = bytearray(unwrap_dek(
            descriptor.wrapped_dek, object_id=object_id, kek=descriptor.kek, resolver=self._resolver,
        ))
        path = self._path(object_id)
        try:
            self._safe_regular(path)
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                file_descriptor = os.open(path, flags)
            except OSError:
                raise StagingIOError("object_unavailable") from None
            try:
                opened = os.fstat(file_descriptor)
                current = self._safe_regular(path)
                if (opened.st_ino, opened.st_dev, opened.st_nlink) != (current.st_ino, current.st_dev, 1):
                    raise StagingSecurityError("unsafe_storage_object")
                with os.fdopen(file_descriptor, "rb", buffering=0) as source:
                    file_descriptor = -1
                    header = _read_exact(source, HEADER.size)
                    magic, version, chunk_size, identity = HEADER.unpack(header)
                    if (magic != MAGIC or version != FORMAT_VERSION or chunk_size != descriptor.chunk_size
                            or identity.hex() != object_id):
                        raise StagingIntegrityError("header_invalid")
                    digest = hashlib.sha256()
                    size = 0
                    index = 0
                    while True:
                        marker = _read_exact(source, 1)[0]
                        if marker == CHUNK_MARKER:
                            rest = _read_exact(source, CHUNK_HEADER.size - 1)
                            parsed = CHUNK_HEADER.unpack(bytes([marker]) + rest)
                            _, frame_index, plaintext_length, ciphertext_length, nonce = parsed
                            if (frame_index != index or plaintext_length > chunk_size
                                    or ciphertext_length != plaintext_length + TAG_BYTES
                                    or size + plaintext_length > max_bytes):
                                raise StagingIntegrityError("chunk_sequence_invalid")
                            ciphertext = _read_exact(source, ciphertext_length)
                            try:
                                plaintext = AESGCM(bytes(dek)).decrypt(
                                    nonce, ciphertext,
                                    _chunk_aad(header, object_id, index, plaintext_length),
                                )
                            except Exception:
                                raise StagingIntegrityError("chunk_authentication_failed") from None
                            if len(plaintext) != plaintext_length:
                                raise StagingIntegrityError("chunk_length_invalid")
                            digest.update(plaintext)
                            size += len(plaintext)
                            index += 1
                            yield plaintext
                            continue
                        if marker != FOOTER_MARKER:
                            raise StagingIntegrityError("frame_type_invalid")
                        rest = _read_exact(source, FOOTER_HEADER.size - 1)
                        _, chunk_count, footer_length, nonce = FOOTER_HEADER.unpack(bytes([marker]) + rest)
                        if chunk_count != index or not TAG_BYTES <= footer_length <= MAX_FOOTER_CIPHERTEXT:
                            raise StagingIntegrityError("footer_invalid")
                        encrypted_footer = _read_exact(source, footer_length)
                        try:
                            raw = AESGCM(bytes(dek)).decrypt(
                                nonce, encrypted_footer, _footer_aad(header, object_id, chunk_count),
                            )
                            if len(raw) > MAX_FOOTER_CIPHERTEXT:
                                raise ValueError
                            envelope = json.loads(raw.decode("ascii"))
                        except Exception:
                            raise StagingIntegrityError("footer_authentication_failed") from None
                        if (not isinstance(envelope, dict) or set(envelope) != {"chunks", "sha256", "size"}
                                or envelope["chunks"] != index or envelope["size"] != size
                                or envelope["sha256"] != digest.hexdigest()):
                            raise StagingIntegrityError("footer_mismatch")
                        if source.read(1):
                            raise StagingIntegrityError("trailing_ciphertext")
                        break
            finally:
                if file_descriptor >= 0:
                    os.close(file_descriptor)
        finally:
            for offset in range(len(dek)):
                dek[offset] = 0

    def delete(self, object_id: str) -> None:
        object_id = _valid_id(object_id)
        try:
            path = self._path(object_id)
        except FileNotFoundError:
            return
        if self._safe_regular(path, allow_missing=True) is None:
            return
        try:
            path.unlink()
            self._fsync_directory(path.parent)
        except FileNotFoundError:
            return
        except OSError:
            raise StagingIOError("delete_failed") from None

    def cleanup_partials(self, object_id: str, *, eligible_fences: Collection[str],
                         active_fences: Collection[str]) -> int:
        object_id = _valid_id(object_id)
        if isinstance(eligible_fences, (str, bytes)) or isinstance(active_fences, (str, bytes)):
            raise StagingSecurityError("invalid_cleanup_scope")
        try:
            if len(eligible_fences) > MAX_CLEANUP_FENCES or len(active_fences) > MAX_CLEANUP_FENCES:
                raise StagingSecurityError("invalid_cleanup_scope")
            eligible = {_valid_id(value) for value in eligible_fences}
            active = {_valid_id(value) for value in active_fences}
        except TypeError:
            raise StagingSecurityError("invalid_cleanup_scope") from None
        deleted = 0
        for fence in sorted(eligible - active):
            try:
                path = self._partial(object_id, fence)
            except FileNotFoundError:
                continue
            if self._safe_regular(path, allow_missing=True) is None:
                continue
            try:
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                continue
            except OSError:
                raise StagingIOError("partial_cleanup_failed") from None
        if deleted:
            self._fsync_directory(self._root / object_id[:2])
        return deleted
