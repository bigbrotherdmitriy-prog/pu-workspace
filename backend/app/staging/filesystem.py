from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import stat
import struct
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Collection, Iterator

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

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
from app.staging.crypto import (
    WRAPPED_DEK_BYTES,
    decode_wrapped_dek,
    unwrap_dek,
    validate_kek_ref,
    wrap_dek,
)

MAGIC = b"PUWSTG2\x00"
FORMAT_VERSION = 2
DEFAULT_CHUNK_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 8 * 1024 * 1024
MAX_OBJECT_BYTES = 1 << 40
MAX_FOOTER_CIPHERTEXT = 512
MAX_CLEANUP_FENCES = 1024
MAX_CHUNKS = (1 << 32) - 1
TAG_BYTES = 16
NONCE_BYTES = 12
ID_RE = re.compile(r"^[0-9a-f]{32}$")

HEADER = struct.Struct(f">8sBI16s16s{WRAPPED_DEK_BYTES}s")
CHUNK_HEADER = struct.Struct(">BQI12s")
FOOTER_HEADER = struct.Struct(">BQI12s")
FOOTER_ENVELOPE = struct.Struct(">QQ32s")
CHUNK_MARKER = 1
FOOTER_MARKER = 2
CHUNK_DOMAIN = b"PUW-STAGING-CHUNK-V2\x00"
FOOTER_DOMAIN = b"PUW-STAGING-FOOTER-V2\x00"
CONTENT_KEY_DOMAIN = b"PUW-STAGING-CONTENT-KEY-V2\x00"
FOOTER_NONCE = b"\xff" * NONCE_BYTES


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


def _write_all(target, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = target.write(value[offset:])
        if (isinstance(written, bool) or not isinstance(written, int)
                or written <= 0 or written > len(value) - offset):
            raise StagingIOError("write_failed")
        offset += written


def _read_plaintext_chunk(source, chunk_size: int, remaining_limit: int) -> tuple[bytes, bool]:
    value = bytearray()
    while len(value) < chunk_size:
        request = min(chunk_size - len(value), remaining_limit - len(value) + 1)
        if request <= 0:
            request = 1
        try:
            part = source.read(request)
        except Exception:
            raise StagingIOError("plaintext_stream_failed") from None
        if not isinstance(part, bytes) or len(part) > request:
            raise StagingIOError("plaintext_stream_failed")
        if not part:
            return bytes(value), True
        value.extend(part)
        if len(value) > remaining_limit:
            raise StagingLimitExceeded("object_too_large")
    return bytes(value), False


def _chunk_nonce(index: int) -> bytes:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < MAX_CHUNKS:
        raise StagingLimitExceeded("too_many_chunks")
    return index.to_bytes(NONCE_BYTES, "big")


def _content_key(dek: bytes, object_id: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=CONTENT_KEY_DOMAIN,
        info=CONTENT_KEY_DOMAIN + object_id.encode("ascii"),
    ).derive(dek)


def _chunk_aad(header: bytes, object_id: str, index: int, padded_length: int) -> bytes:
    return (CHUNK_DOMAIN + header + object_id.encode("ascii")
            + struct.pack(">QI", index, padded_length))


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
            prepared = self._root.stat()
            if os.name == "posix" and stat.S_IMODE(prepared.st_mode) != 0o700:
                raise StagingSecurityError("unsafe_storage_permissions")
            self._root_identity = (prepared.st_dev, prepared.st_ino)
        except StagingError:
            raise
        except OSError:
            raise StagingIOError("storage_root_unavailable") from None

    def _check_root(self):
        try:
            value = self._root.lstat()
            if _is_linklike(self._root) or stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                raise StagingSecurityError("unsafe_storage_root")
            if (value.st_dev, value.st_ino) != self._root_identity:
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
            value = shard.lstat()
            if (_is_linklike(shard) or stat.S_ISLNK(value.st_mode)
                    or not stat.S_ISDIR(value.st_mode)):
                raise StagingSecurityError("unsafe_storage_shard")
            if create:
                os.chmod(shard, 0o700)
                if _is_linklike(shard):
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

    @contextmanager
    def _opened_shard(self, object_id: str, *, create: bool):
        """Anchor POSIX operations to verified directory descriptors.

        Windows has no Python dir_fd support, so every effect is surrounded by
        reparse-point checks in the name helpers below.
        """
        object_id = _valid_id(object_id)
        if os.name != "posix":
            try:
                path = self._shard(object_id, create=create)
            except FileNotFoundError:
                raise StagingIOError("object_unavailable") from None
            yield path, None
            return

        root_fd = shard_fd = -1
        try:
            try:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                root_fd = os.open(self._root, flags)
                root_opened = os.fstat(root_fd)
                root_named = self._root.lstat()
                if ((root_opened.st_dev, root_opened.st_ino) != (root_named.st_dev, root_named.st_ino)
                        or (root_opened.st_dev, root_opened.st_ino) != self._root_identity
                        or not stat.S_ISDIR(root_opened.st_mode)
                        or stat.S_IMODE(root_opened.st_mode) & 0o077):
                    raise StagingSecurityError("unsafe_storage_root")
                shard_name = object_id[:2]
                if create:
                    try:
                        os.mkdir(shard_name, 0o700, dir_fd=root_fd)
                    except FileExistsError:
                        pass
                named = os.stat(shard_name, dir_fd=root_fd, follow_symlinks=False)
                if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode):
                    raise StagingSecurityError("unsafe_storage_shard")
                shard_fd = os.open(shard_name, flags, dir_fd=root_fd)
                opened = os.fstat(shard_fd)
                if ((opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                        or not stat.S_ISDIR(opened.st_mode)
                        or stat.S_IMODE(opened.st_mode) & 0o077):
                    raise StagingSecurityError("unsafe_storage_shard")
            except FileNotFoundError:
                raise StagingIOError("object_unavailable") from None
            except StagingError:
                raise
            except OSError:
                raise StagingIOError("storage_shard_unavailable") from None
            yield self._root / shard_name, shard_fd
        finally:
            if shard_fd >= 0:
                os.close(shard_fd)
            if root_fd >= 0:
                os.close(root_fd)

    def _verify_windows_shard(self, shard) -> Path:
        path, descriptor = shard
        if descriptor is not None:
            return path
        self._check_root()
        try:
            value = path.lstat()
            if (_is_linklike(path) or stat.S_ISLNK(value.st_mode)
                    or not stat.S_ISDIR(value.st_mode)):
                raise StagingSecurityError("unsafe_storage_shard")
        except StagingError:
            raise
        except OSError:
            raise StagingIOError("storage_shard_unavailable") from None
        return path

    def _lstat_name(self, shard, name: str, *, allow_missing=False):
        path, descriptor = shard
        try:
            if descriptor is not None:
                return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            return (self._verify_windows_shard(shard) / name).lstat()
        except FileNotFoundError:
            if allow_missing:
                return None
            raise StagingIOError("object_unavailable") from None
        except StagingError:
            raise
        except OSError:
            raise StagingIOError("object_unavailable") from None

    def _safe_regular_name(self, shard, name: str, *, allow_missing=False, links=1):
        value = self._lstat_name(shard, name, allow_missing=allow_missing)
        if value is None:
            return None
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode) or value.st_nlink != links:
            raise StagingSecurityError("unsafe_storage_object")
        return value

    def _open_name(self, shard, name: str, flags: int, mode=0o600) -> int:
        path, descriptor = shard
        try:
            flags |= getattr(os, "O_NOFOLLOW", 0)
            if descriptor is not None:
                return os.open(name, flags, mode, dir_fd=descriptor)
            return os.open(self._verify_windows_shard(shard) / name, flags, mode)
        except StagingError:
            raise
        except OSError:
            raise StagingIOError("object_unavailable") from None

    def _link_names(self, shard, source_name: str, target_name: str) -> None:
        path, descriptor = shard
        if descriptor is not None:
            os.link(
                source_name, target_name, src_dir_fd=descriptor,
                dst_dir_fd=descriptor, follow_symlinks=False,
            )
            return
        base = self._verify_windows_shard(shard)
        os.link(base / source_name, base / target_name, follow_symlinks=False)
        self._verify_windows_shard(shard)

    def _unlink_name(self, shard, name: str) -> None:
        path, descriptor = shard
        if descriptor is not None:
            os.unlink(name, dir_fd=descriptor)
            return
        (self._verify_windows_shard(shard) / name).unlink()
        self._verify_windows_shard(shard)

    @staticmethod
    def _fsync_shard(shard) -> None:
        _, descriptor = shard
        if descriptor is not None:
            try:
                os.fsync(descriptor)
            except OSError:
                raise StagingIOError("directory_sync_failed") from None

    @staticmethod
    def _descriptor_from_header(header: bytes, *, object_id: str, kek: KekRef):
        try:
            magic, version, chunk_size, identity, fence, wrapped = HEADER.unpack(header)
        except Exception:
            raise StagingIntegrityError("header_invalid") from None
        if (magic != MAGIC or version != FORMAT_VERSION or identity.hex() != object_id
                or not 1 <= chunk_size <= MAX_CHUNK_BYTES):
            raise StagingIntegrityError("header_invalid")
        return StagingDescriptor(
            object_id=object_id, format_version=version, chunk_size=chunk_size, kek=kek,
            wrapped_dek=base64.urlsafe_b64encode(wrapped).decode("ascii"),
        ), fence.hex()

    def _validate_opened_object(self, shard, name: str, file_descriptor: int, fence: str):
        opened = os.fstat(file_descriptor)
        current = self._lstat_name(shard, name)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink not in (1, 2)
                or (opened.st_dev, opened.st_ino, opened.st_nlink)
                != (current.st_dev, current.st_ino, current.st_nlink)):
            raise StagingSecurityError("unsafe_storage_object")
        if opened.st_nlink == 2:
            partial_name = f"{name[:-4]}.partial.{fence}"
            partial = self._lstat_name(shard, partial_name, allow_missing=True)
            if (partial is None or not stat.S_ISREG(partial.st_mode) or partial.st_nlink != 2
                    or (partial.st_dev, partial.st_ino) != (opened.st_dev, opened.st_ino)):
                raise StagingSecurityError("unsafe_storage_object")
        return opened

    def _verify_stream(self, source, descriptor, dek: bytes, *, max_bytes: int) -> int:
        object_id = descriptor.object_id
        header = _read_exact(source, HEADER.size)
        parsed, _ = self._descriptor_from_header(header, object_id=object_id, kek=descriptor.kek)
        if (parsed.chunk_size != descriptor.chunk_size
                or parsed.wrapped_dek != descriptor.wrapped_dek):
            raise StagingIntegrityError("header_invalid")
        content_key = bytearray(_content_key(dek, object_id))
        digest = hashlib.sha256()
        confirmed_size = 0
        index = 0
        last = None
        try:
            while True:
                marker = _read_exact(source, 1)[0]
                if marker == CHUNK_MARKER:
                    rest = _read_exact(source, CHUNK_HEADER.size - 1)
                    _, frame_index, ciphertext_length, nonce = CHUNK_HEADER.unpack(bytes([marker]) + rest)
                    if (frame_index != index or index >= MAX_CHUNKS
                            or ciphertext_length != descriptor.chunk_size + TAG_BYTES
                            or nonce != _chunk_nonce(index)):
                        raise StagingIntegrityError("chunk_sequence_invalid")
                    ciphertext = _read_exact(source, ciphertext_length)
                    try:
                        plaintext = AESGCM(bytes(content_key)).decrypt(
                            nonce, ciphertext,
                            _chunk_aad(header, object_id, index, descriptor.chunk_size),
                        )
                    except Exception:
                        raise StagingIntegrityError("chunk_authentication_failed") from None
                    if len(plaintext) != descriptor.chunk_size:
                        raise StagingIntegrityError("chunk_length_invalid")
                    if last is not None:
                        digest.update(last)
                        confirmed_size += len(last)
                        if confirmed_size > max_bytes:
                            raise StagingLimitExceeded("object_too_large")
                    last = plaintext
                    index += 1
                    continue
                if marker != FOOTER_MARKER:
                    raise StagingIntegrityError("frame_type_invalid")
                rest = _read_exact(source, FOOTER_HEADER.size - 1)
                _, chunk_count, footer_length, nonce = FOOTER_HEADER.unpack(bytes([marker]) + rest)
                if (chunk_count != index or index < 1 or nonce != FOOTER_NONCE
                        or footer_length != FOOTER_ENVELOPE.size + TAG_BYTES):
                    raise StagingIntegrityError("footer_invalid")
                encrypted_footer = _read_exact(source, footer_length)
                try:
                    raw = AESGCM(bytes(content_key)).decrypt(
                        nonce, encrypted_footer, _footer_aad(header, object_id, chunk_count),
                    )
                    envelope_chunks, envelope_size, envelope_digest = FOOTER_ENVELOPE.unpack(raw)
                except Exception:
                    raise StagingIntegrityError("footer_authentication_failed") from None
                if envelope_chunks != index or not 0 <= envelope_size <= MAX_OBJECT_BYTES:
                    raise StagingIntegrityError("footer_mismatch")
                if envelope_size > max_bytes:
                    raise StagingLimitExceeded("object_too_large")
                tail_size = envelope_size - confirmed_size
                if last is None or not 0 <= tail_size <= descriptor.chunk_size:
                    raise StagingIntegrityError("footer_mismatch")
                if index > 1 and tail_size == 0:
                    raise StagingIntegrityError("footer_mismatch")
                if any(last[tail_size:]):
                    raise StagingIntegrityError("padding_invalid")
                digest.update(last[:tail_size])
                if envelope_digest != digest.digest():
                    raise StagingIntegrityError("footer_mismatch")
                if source.read(1):
                    raise StagingIntegrityError("trailing_ciphertext")
                return envelope_size
        finally:
            for offset in range(len(content_key)):
                content_key[offset] = 0

    def _yield_verified_stream(self, source, descriptor, dek: bytes, *, size: int):
        object_id = descriptor.object_id
        header = _read_exact(source, HEADER.size)
        parsed, _ = self._descriptor_from_header(header, object_id=object_id, kek=descriptor.kek)
        if parsed != descriptor:
            raise StagingIntegrityError("header_invalid")
        content_key = bytearray(_content_key(dek, object_id))
        index = 0
        remaining = size
        try:
            while True:
                marker = _read_exact(source, 1)[0]
                if marker == CHUNK_MARKER:
                    rest = _read_exact(source, CHUNK_HEADER.size - 1)
                    _, frame_index, ciphertext_length, nonce = CHUNK_HEADER.unpack(bytes([marker]) + rest)
                    if (frame_index != index or ciphertext_length != descriptor.chunk_size + TAG_BYTES
                            or nonce != _chunk_nonce(index)):
                        raise StagingIntegrityError("chunk_sequence_invalid")
                    ciphertext = _read_exact(source, ciphertext_length)
                    try:
                        padded = AESGCM(bytes(content_key)).decrypt(
                            nonce, ciphertext,
                            _chunk_aad(header, object_id, index, descriptor.chunk_size),
                        )
                    except Exception:
                        raise StagingIntegrityError("chunk_authentication_failed") from None
                    amount = min(remaining, descriptor.chunk_size)
                    if amount:
                        yield padded[:amount]
                    remaining -= amount
                    index += 1
                    continue
                if marker != FOOTER_MARKER:
                    raise StagingIntegrityError("frame_type_invalid")
                rest = _read_exact(source, FOOTER_HEADER.size - 1)
                _, chunk_count, footer_length, nonce = FOOTER_HEADER.unpack(bytes([marker]) + rest)
                if (chunk_count != index or remaining != 0 or nonce != FOOTER_NONCE
                        or footer_length != FOOTER_ENVELOPE.size + TAG_BYTES):
                    raise StagingIntegrityError("footer_invalid")
                encrypted_footer = _read_exact(source, footer_length)
                try:
                    AESGCM(bytes(content_key)).decrypt(
                        nonce, encrypted_footer, _footer_aad(header, object_id, chunk_count),
                    )
                except Exception:
                    raise StagingIntegrityError("footer_authentication_failed") from None
                if source.read(1):
                    raise StagingIntegrityError("trailing_ciphertext")
                return
        finally:
            for offset in range(len(content_key)):
                content_key[offset] = 0

    def _read_header(self, shard, final_name: str, object_id: str, kek: KekRef):
        file_descriptor = self._open_name(shard, final_name, os.O_RDONLY)
        try:
            with os.fdopen(os.dup(file_descriptor), "rb", buffering=0) as source:
                header = _read_exact(source, HEADER.size)
            descriptor, fence = self._descriptor_from_header(header, object_id=object_id, kek=kek)
            self._validate_opened_object(shard, final_name, file_descriptor, fence)
            return descriptor, fence
        finally:
            os.close(file_descriptor)

    def write(self, object_id: str, source: BinaryIO, *, max_bytes: int, kek: KekRef,
              fence: str) -> StagingDescriptor:
        object_id, fence = _valid_id(object_id), _valid_id(fence)
        max_bytes = _valid_limit(max_bytes)
        validate_kek_ref(kek)
        if not callable(getattr(source, "read", None)):
            raise StagingSecurityError("invalid_plaintext_stream")
        final_name = f"{object_id}.enc"
        partial_name = f"{object_id}.partial.{fence}"

        dek = bytearray(secrets.token_bytes(32))
        published = False
        created_partial = False
        try:
            wrapped = wrap_dek(bytes(dek), object_id=object_id, kek=kek, resolver=self._resolver)
            header = HEADER.pack(
                MAGIC, FORMAT_VERSION, self._chunk_size, bytes.fromhex(object_id),
                bytes.fromhex(fence), decode_wrapped_dek(wrapped),
            )
            with self._opened_shard(object_id, create=True) as opened_shard:
                shard = opened_shard
                existing = self._lstat_name(shard, final_name, allow_missing=True)
                if existing is not None:
                    recovered, existing_fence = self._read_header(shard, final_name, object_id, kek)
                    if existing_fence != fence:
                        raise StagingConflict("object_exists")
                    for _ in self.read_chunks(recovered, max_bytes=max_bytes):
                        pass
                    published = True
                    partial = self._lstat_name(shard, partial_name, allow_missing=True)
                    if partial is not None and partial.st_nlink == 2:
                        try:
                            self._unlink_name(shard, partial_name)
                            self._fsync_shard(shard)
                        except OSError:
                            pass
                    return recovered
                if self._safe_regular_name(shard, partial_name, allow_missing=True) is not None:
                    raise StagingConflict("writer_fence_exists")

                digest = hashlib.sha256()
                size = 0
                index = 0
                content_key = bytearray(_content_key(bytes(dek), object_id))
                try:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    file_descriptor = self._open_name(shard, partial_name, flags)
                    created_partial = True
                    with os.fdopen(file_descriptor, "wb", buffering=0) as target:
                        if os.name == "posix":
                            os.fchmod(target.fileno(), 0o600)
                        _write_all(target, header)
                        while True:
                            chunk, eof = _read_plaintext_chunk(
                                source, self._chunk_size, max_bytes - size,
                            )
                            if not chunk and index:
                                break
                            nonce = _chunk_nonce(index)
                            padded = chunk + b"\x00" * (self._chunk_size - len(chunk))
                            ciphertext = AESGCM(bytes(content_key)).encrypt(
                                nonce, padded,
                                _chunk_aad(header, object_id, index, self._chunk_size),
                            )
                            _write_all(target, CHUNK_HEADER.pack(
                                CHUNK_MARKER, index, len(ciphertext), nonce,
                            ))
                            _write_all(target, ciphertext)
                            digest.update(chunk)
                            size += len(chunk)
                            index += 1
                            if eof:
                                break
                        envelope = FOOTER_ENVELOPE.pack(index, size, digest.digest())
                        encrypted_footer = AESGCM(bytes(content_key)).encrypt(
                            FOOTER_NONCE, envelope, _footer_aad(header, object_id, index),
                        )
                        if len(encrypted_footer) > MAX_FOOTER_CIPHERTEXT:
                            raise StagingIntegrityError("footer_too_large")
                        _write_all(target, FOOTER_HEADER.pack(
                            FOOTER_MARKER, index, len(encrypted_footer), FOOTER_NONCE,
                        ))
                        _write_all(target, encrypted_footer)
                        target.flush()
                        os.fsync(target.fileno())
                finally:
                    for offset in range(len(content_key)):
                        content_key[offset] = 0
                self._safe_regular_name(shard, partial_name)
                try:
                    self._link_names(shard, partial_name, final_name)
                except FileExistsError:
                    raise StagingConflict("object_exists") from None
                except OSError:
                    raise StagingIOError("publish_failed") from None
                published = True
                try:
                    self._unlink_name(shard, partial_name)
                except OSError:
                    # The final file is complete and its authenticated header
                    # records this exact fence; a retry can finish cleanup.
                    pass
                self._fsync_shard(shard)
                if os.name != "posix":
                    self._fsync_directory(shard[0])
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
            primary_error = sys.exc_info()[0] is not None
            for offset in range(len(dek)):
                dek[offset] = 0
            if created_partial and not published:
                try:
                    with self._opened_shard(object_id, create=False) as cleanup_shard:
                        if self._safe_regular_name(
                                cleanup_shard, partial_name, allow_missing=True) is not None:
                            self._unlink_name(cleanup_shard, partial_name)
                except StagingIOError as error:
                    if str(error) != "object_unavailable" and not primary_error:
                        raise StagingIOError("partial_cleanup_failed") from None
                except Exception:
                    if not primary_error:
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
        decode_wrapped_dek(descriptor.wrapped_dek)
        dek = bytearray(unwrap_dek(
            descriptor.wrapped_dek, object_id=object_id, kek=descriptor.kek, resolver=self._resolver,
        ))
        final_name = f"{object_id}.enc"
        try:
            with self._opened_shard(object_id, create=False) as shard:
                file_descriptor = self._open_name(shard, final_name, os.O_RDONLY)
                try:
                    with os.fdopen(file_descriptor, "rb", buffering=0) as source:
                        file_descriptor = -1
                        header = _read_exact(source, HEADER.size)
                        parsed, fence = self._descriptor_from_header(
                            header, object_id=object_id, kek=descriptor.kek,
                        )
                        if parsed != descriptor:
                            raise StagingIntegrityError("header_invalid")
                        opened = self._validate_opened_object(
                            shard, final_name, source.fileno(), fence,
                        )
                        source.seek(0)
                        size = self._verify_stream(
                            source, descriptor, bytes(dek), max_bytes=max_bytes,
                        )
                        verified = os.fstat(source.fileno())
                        signature = lambda value: (
                            value.st_dev, value.st_ino, value.st_size,
                            value.st_mtime_ns, value.st_ctime_ns,
                        )
                        if signature(opened) != signature(verified):
                            raise StagingSecurityError("object_changed_during_read")
                        source.seek(0)
                        yield from self._yield_verified_stream(
                            source, descriptor, bytes(dek), size=size,
                        )
                        completed = os.fstat(source.fileno())
                        if signature(verified) != signature(completed):
                            raise StagingSecurityError("object_changed_during_read")
                finally:
                    if file_descriptor >= 0:
                        os.close(file_descriptor)
        finally:
            for offset in range(len(dek)):
                dek[offset] = 0

    def delete(self, object_id: str) -> None:
        object_id = _valid_id(object_id)
        try:
            with self._opened_shard(object_id, create=False) as shard:
                final_name = f"{object_id}.enc"
                value = self._lstat_name(shard, final_name, allow_missing=True)
                if value is None:
                    return
                if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
                    raise StagingSecurityError("unsafe_storage_object")
                if value.st_nlink == 2:
                    file_descriptor = self._open_name(shard, final_name, os.O_RDONLY)
                    try:
                        with os.fdopen(os.dup(file_descriptor), "rb", buffering=0) as source:
                            header = _read_exact(source, HEADER.size)
                        magic, version, _, identity, fence_raw, _ = HEADER.unpack(header)
                        if magic != MAGIC or version != FORMAT_VERSION or identity.hex() != object_id:
                            raise StagingSecurityError("unsafe_storage_object")
                        fence = fence_raw.hex()
                        self._validate_opened_object(shard, final_name, file_descriptor, fence)
                    finally:
                        os.close(file_descriptor)
                    self._unlink_name(shard, f"{object_id}.partial.{fence}")
                elif value.st_nlink != 1:
                    raise StagingSecurityError("unsafe_storage_object")
                self._unlink_name(shard, final_name)
                self._fsync_shard(shard)
                if os.name != "posix":
                    self._fsync_directory(shard[0])
        except StagingIOError as error:
            if str(error) == "object_unavailable":
                return
            raise
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
        try:
            with self._opened_shard(object_id, create=False) as shard:
                for fence in sorted(eligible - active):
                    name = f"{object_id}.partial.{fence}"
                    value = self._lstat_name(shard, name, allow_missing=True)
                    if value is None:
                        continue
                    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
                        raise StagingSecurityError("unsafe_storage_object")
                    if value.st_nlink == 2:
                        final = self._lstat_name(shard, f"{object_id}.enc", allow_missing=True)
                        if (final is None or final.st_nlink != 2
                                or (final.st_dev, final.st_ino) != (value.st_dev, value.st_ino)):
                            raise StagingSecurityError("unsafe_storage_object")
                    elif value.st_nlink != 1:
                        raise StagingSecurityError("unsafe_storage_object")
                    try:
                        self._unlink_name(shard, name)
                        deleted += 1
                    except FileNotFoundError:
                        continue
                    except OSError:
                        raise StagingIOError("partial_cleanup_failed") from None
                if deleted:
                    self._fsync_shard(shard)
                    if os.name != "posix":
                        self._fsync_directory(shard[0])
        except StagingIOError as error:
            if str(error) != "object_unavailable":
                raise
        return deleted
