from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import os
from pathlib import Path
import stat
from threading import Barrier, Lock, Thread

import pytest

from app.staging.contracts import (
    KekRef, StagingConflict, StagingIOError, StagingIntegrityError,
    StagingLimitExceeded, StagingSecurityError, StagingStorage,
)
from app.staging.filesystem import (
    CHUNK_HEADER, CHUNK_MARKER, FOOTER_HEADER, HEADER,
    FilesystemStagingStorage, _write_all, new_fence, new_object_id,
)
from app.staging.crypto import unwrap_dek


class Keys:
    def __init__(self, values=None):
        self.values = values or {("kms/test", "v1"): b"k" * 32}

    def resolve(self, reference, version):
        return self.values[(reference, version)]


@pytest.fixture
def store(tmp_path):
    return FilesystemStagingStorage(tmp_path / "private", Keys(), chunk_size=8)


def write(store, data, *, object_id=None, fence=None, max_bytes=None, kek=None):
    return store.write(
        object_id or new_object_id(), BytesIO(data), max_bytes=len(data) if max_bytes is None else max_bytes,
        kek=kek or KekRef("kms/test", "v1"), fence=fence or new_fence(),
    )


def read(store, descriptor, max_bytes=10_000):
    return b"".join(store.read_chunks(descriptor, max_bytes=max_bytes))


def path_for(store, descriptor):
    root = store._root
    return root / descriptor.object_id[:2] / f"{descriptor.object_id}.enc"


@pytest.mark.parametrize("data", [b"", b"x", b"12345678", b"123456789", b"abcdefgh" * 5 + b"z"])
def test_streaming_round_trip_empty_one_boundary_and_multichunk(store, data):
    descriptor = write(store, data)
    assert read(store, descriptor, max_bytes=len(data)) == data
    assert descriptor.format_version == 2 and descriptor.chunk_size == 8


def test_ciphertext_and_descriptor_do_not_expose_plaintext_or_business_metadata(store):
    plaintext = b"TOP-SECRET-CONTENT-UNIQUE"
    descriptor = write(store, plaintext)
    ciphertext = path_for(store, descriptor).read_bytes()
    assert plaintext not in ciphertext
    assert plaintext.decode() not in repr(descriptor)
    assert "wrapped_dek=<" not in repr(descriptor) and "wrapped_dek" not in repr(descriptor)
    fields = set(descriptor.__dataclass_fields__)
    assert not fields.intersection({"filename", "mime", "provider", "locator", "owner", "project", "url", "path", "size", "sha256"})


def test_padding_hides_exact_plaintext_size_within_one_chunk_bucket(store):
    first = write(store, b"a")
    second = write(store, b"1234567")
    first_raw = path_for(store, first).read_bytes()
    second_raw = path_for(store, second).read_bytes()
    assert len(first_raw) == len(second_raw)
    assert b'"size":1' not in first_raw
    assert b'"size":7' not in second_raw


class BoundedStream:
    def __init__(self, value, maximum):
        self.value = BytesIO(value)
        self.maximum = maximum
        self.requests = []

    def read(self, amount):
        assert isinstance(amount, int) and 0 < amount <= self.maximum
        self.requests.append(amount)
        return self.value.read(amount)


def test_writer_never_uses_unbounded_read(store):
    source = BoundedStream(b"a" * 41, 8)
    descriptor = store.write(new_object_id(), source, max_bytes=41,
                             kek=KekRef("kms/test", "v1"), fence=new_fence())
    assert read(store, descriptor) == b"a" * 41
    assert source.requests and max(source.requests) <= 8


def test_writer_coalesces_legal_short_source_reads_before_padding(store):
    class OneByteAtATime(BytesIO):
        def read(self, amount):
            return super().read(min(amount, 1))

    data = b"short reads are not eof"
    descriptor = store.write(
        new_object_id(), OneByteAtATime(data), max_bytes=len(data),
        kek=KekRef("kms/test", "v1"), fence=new_fence(),
    )
    assert read(store, descriptor) == data


def test_write_all_retries_short_writes():
    class ShortWriter:
        def __init__(self):
            self.value = bytearray()

        def write(self, value):
            amount = min(2, len(value))
            self.value.extend(value[:amount])
            return amount

    target = ShortWriter()
    _write_all(target, b"abcdef")
    assert bytes(target.value) == b"abcdef"


def test_oversized_stream_removes_its_exact_partial_and_does_not_publish(store):
    object_id, fence = new_object_id(), new_fence()
    with pytest.raises(StagingLimitExceeded, match="object_too_large"):
        store.write(object_id, BytesIO(b"123456789"), max_bytes=8,
                    kek=KekRef("kms/test", "v1"), fence=fence)
    shard = store._root / object_id[:2]
    assert not (shard / f"{object_id}.enc").exists()
    assert not (shard / f"{object_id}.partial.{fence}").exists()


def test_writer_never_removes_a_preexisting_same_fence_partial(store):
    object_id, fence = new_object_id(), new_fence()
    shard = store._root / object_id[:2]
    shard.mkdir()
    if os.name == "posix":
        shard.chmod(0o700)
    partial = shard / f"{object_id}.partial.{fence}"
    partial.write_bytes(b"another-active-writer")
    with pytest.raises(StagingConflict, match="writer_fence_exists"):
        write(store, b"replacement", object_id=object_id, fence=fence)
    assert partial.read_bytes() == b"another-active-writer"


def test_reader_enforces_max_size_during_stream(store):
    descriptor = write(store, b"123456789")
    with pytest.raises(StagingLimitExceeded, match="object_too_large"):
        read(store, descriptor, max_bytes=8)


def frames(raw):
    output = []
    position = HEADER.size
    while position < len(raw):
        marker = raw[position]
        if marker == CHUNK_MARKER:
            header = raw[position:position + CHUNK_HEADER.size]
            _, _, encrypted_length, _ = CHUNK_HEADER.unpack(header)
            end = position + CHUNK_HEADER.size + encrypted_length
        else:
            header = raw[position:position + FOOTER_HEADER.size]
            _, _, encrypted_length, _ = FOOTER_HEADER.unpack(header)
            end = position + FOOTER_HEADER.size + encrypted_length
        output.append(raw[position:end])
        position = end
    return raw[:HEADER.size], output


def mutate_file(store, descriptor, transform):
    path = path_for(store, descriptor)
    path.write_bytes(transform(path.read_bytes()))


@pytest.mark.parametrize("target", ["header", "chunk_header", "chunk_nonce", "chunk_tag", "footer_nonce", "footer_tag"])
def test_tampered_header_chunk_nonce_tag_and_footer_are_rejected(store, target):
    descriptor = write(store, b"0123456789abcdef")
    def tamper(raw):
        header, records = frames(raw)
        value = bytearray(raw)
        if target == "header":
            offset = 8
        elif target == "chunk_header":
            offset = len(header) + 2
        elif target == "chunk_nonce":
            offset = len(header) + CHUNK_HEADER.size - 1
        elif target == "chunk_tag":
            offset = len(header) + len(records[0]) - 1
        elif target == "footer_nonce":
            offset = len(raw) - len(records[-1]) + FOOTER_HEADER.size - 1
        else:
            offset = len(raw) - 1
        value[offset] ^= 1
        return bytes(value)
    mutate_file(store, descriptor, tamper)
    with pytest.raises(StagingIntegrityError):
        read(store, descriptor)


def test_chunk_and_footer_nonces_are_disjoint_deterministic_counters(store):
    descriptor = write(store, b"0123456789abcdef")
    _, records = frames(path_for(store, descriptor).read_bytes())
    nonces = []
    for record in records[:-1]:
        _, index, _, nonce = CHUNK_HEADER.unpack(record[:CHUNK_HEADER.size])
        assert nonce == index.to_bytes(12, "big")
        nonces.append(nonce)
    _, _, _, footer_nonce = FOOTER_HEADER.unpack(records[-1][:FOOTER_HEADER.size])
    assert footer_nonce == b"\xff" * 12
    assert footer_nonce not in nonces and len(nonces) == len(set(nonces))


def test_zero_length_chunk_frame_is_rejected_before_decryption(store):
    descriptor = write(store, b"x")
    def transform(raw):
        value = bytearray(raw)
        offset = HEADER.size
        marker, index, _, nonce = CHUNK_HEADER.unpack(
            value[offset:offset + CHUNK_HEADER.size],
        )
        value[offset:offset + CHUNK_HEADER.size] = CHUNK_HEADER.pack(
            marker, index, 16, nonce,
        )
        return bytes(value)
    mutate_file(store, descriptor, transform)
    with pytest.raises(StagingIntegrityError, match="chunk_sequence_invalid"):
        read(store, descriptor)


def test_tampered_wrapped_key_and_cross_object_substitution_are_rejected(store):
    descriptor = write(store, b"classified")
    changed = "A" if descriptor.wrapped_dek[-2] != "A" else "B"
    with pytest.raises(StagingIntegrityError):
        read(store, replace(descriptor, wrapped_dek=descriptor.wrapped_dek[:-2] + changed + descriptor.wrapped_dek[-1]))

    other = new_object_id()
    other_path = store._root / other[:2]
    other_path.mkdir(mode=0o700)
    (other_path / f"{other}.enc").write_bytes(path_for(store, descriptor).read_bytes())
    with pytest.raises(StagingIntegrityError):
        read(store, replace(descriptor, object_id=other))


@pytest.mark.parametrize("operation", ["reorder", "duplicate", "missing"])
def test_reordered_duplicated_and_missing_chunks_are_rejected(store, operation):
    descriptor = write(store, b"abcdefghijklmnop")
    def transform(raw):
        header, records = frames(raw)
        chunks, footer = records[:-1], records[-1]
        if operation == "reorder":
            chunks[0], chunks[1] = chunks[1], chunks[0]
        elif operation == "duplicate":
            chunks.insert(1, chunks[0])
        else:
            chunks.pop(0)
        return header + b"".join(chunks) + footer
    mutate_file(store, descriptor, transform)
    with pytest.raises(StagingIntegrityError):
        read(store, descriptor)


def test_truncation_at_every_byte_boundary_and_trailing_garbage(store):
    descriptor = write(store, b"small payload")
    path = path_for(store, descriptor)
    complete = path.read_bytes()
    for end in range(len(complete)):
        path.write_bytes(complete[:end])
        with pytest.raises((StagingIntegrityError, StagingIOError)):
            read(store, descriptor)
    path.write_bytes(complete + b"garbage")
    with pytest.raises(StagingIntegrityError, match="trailing_ciphertext"):
        read(store, descriptor)


def test_truncated_object_yields_no_plaintext_before_whole_object_verification(store):
    descriptor = write(store, b"0123456789abcdef")
    path = path_for(store, descriptor)
    _, records = frames(path.read_bytes())
    path.write_bytes(path.read_bytes()[:-len(records[-1])])
    iterator = store.read_chunks(descriptor, max_bytes=100)
    with pytest.raises(StagingIntegrityError):
        next(iterator)


def test_wrong_missing_old_key_and_rotation_require_exact_version(tmp_path):
    keys = Keys({("kms/test", "old"): b"o" * 32})
    store = FilesystemStagingStorage(tmp_path / "private", keys, chunk_size=8)
    descriptor = write(store, b"rotation", kek=KekRef("kms/test", "old"))
    keys.values = {("kms/test", "new"): b"n" * 32}
    with pytest.raises(StagingIntegrityError, match="key_unavailable"):
        read(store, descriptor)
    keys.values[("kms/test", "old")] = b"o" * 32
    assert read(store, descriptor) == b"rotation"
    keys.values[("kms/test", "old")] = b"w" * 32
    with pytest.raises(StagingIntegrityError):
        read(store, descriptor)


def test_equal_plaintext_has_distinct_ciphertext_and_keys(store):
    first = write(store, b"same")
    second = write(store, b"same")
    assert path_for(store, first).read_bytes() != path_for(store, second).read_bytes()
    assert first.wrapped_dek != second.wrapped_dek
    first_dek = unwrap_dek(first.wrapped_dek, object_id=first.object_id, kek=first.kek, resolver=store._resolver)
    second_dek = unwrap_dek(second.wrapped_dek, object_id=second.object_id, kek=second.kek, resolver=store._resolver)
    assert first_dek != second_dek


def test_existing_object_is_never_overwritten(store):
    object_id = new_object_id()
    first = write(store, b"original", object_id=object_id)
    before = path_for(store, first).read_bytes()
    with pytest.raises(StagingConflict, match="object_exists"):
        write(store, b"replacement", object_id=object_id)
    assert path_for(store, first).read_bytes() == before
    assert read(store, first) == b"original"


def test_same_fence_recovers_publish_after_partial_unlink_failure(store, monkeypatch):
    object_id, fence = new_object_id(), new_fence()
    original = store._unlink_name
    failed = False

    def fail_once(shard, name):
        nonlocal failed
        if not failed and ".partial." in name:
            failed = True
            raise PermissionError
        return original(shard, name)

    monkeypatch.setattr(store, "_unlink_name", fail_once)
    descriptor = store.write(
        object_id, BytesIO(b"recoverable"), max_bytes=11,
        kek=KekRef("kms/test", "v1"), fence=fence,
    )
    monkeypatch.setattr(store, "_unlink_name", original)
    recovered = store.write(
        object_id, BytesIO(b"ignored-on-idempotent-retry"), max_bytes=100,
        kek=KekRef("kms/test", "v1"), fence=fence,
    )
    assert recovered == descriptor
    assert read(store, recovered) == b"recoverable"
    partial = store._root / object_id[:2] / f"{object_id}.partial.{fence}"
    assert not partial.exists()


def test_same_fence_recovers_ambiguous_directory_sync_failure(store, monkeypatch):
    object_id, fence = new_object_id(), new_fence()
    original = store._fsync_shard
    failed = False

    def fail_once(shard):
        nonlocal failed
        if not failed:
            failed = True
            raise StagingIOError("directory_sync_failed")
        return original(shard)

    monkeypatch.setattr(store, "_fsync_shard", fail_once)
    with pytest.raises(StagingIOError, match="directory_sync_failed"):
        store.write(
            object_id, BytesIO(b"durable"), max_bytes=7,
            kek=KekRef("kms/test", "v1"), fence=fence,
        )
    monkeypatch.setattr(store, "_fsync_shard", original)
    recovered = store.write(
        object_id, BytesIO(b"ignored"), max_bytes=7,
        kek=KekRef("kms/test", "v1"), fence=fence,
    )
    assert read(store, recovered) == b"durable"


def test_strict_chunk_budget_fails_closed_and_removes_partial(store, monkeypatch):
    import app.staging.filesystem as filesystem
    monkeypatch.setattr(filesystem, "MAX_CHUNKS", 2)
    object_id, fence = new_object_id(), new_fence()
    with pytest.raises(StagingLimitExceeded, match="too_many_chunks"):
        store.write(
            object_id, BytesIO(b"x" * 17), max_bytes=17,
            kek=KekRef("kms/test", "v1"), fence=fence,
        )
    shard = store._root / object_id[:2]
    assert not (shard / f"{object_id}.enc").exists()
    assert not (shard / f"{object_id}.partial.{fence}").exists()


def test_existing_object_with_different_fence_is_still_a_conflict(store):
    object_id = new_object_id()
    write(store, b"original", object_id=object_id, fence=new_fence())
    with pytest.raises(StagingConflict, match="object_exists"):
        write(store, b"replacement", object_id=object_id, fence=new_fence())


@pytest.mark.parametrize("value", [
    "", "a", "A" * 32, "0" * 31, "0" * 33, "../" + "0" * 29,
    "0/" + "0" * 30, "0\\" + "0" * 30, "/" + "0" * 32,
    "C:\\" + "0" * 32, "\\\\server\\share", "0" * 16 + ".." + "0" * 14,
])
def test_malformed_traversal_absolute_and_unc_ids_are_rejected(store, value):
    with pytest.raises(StagingSecurityError, match="invalid_opaque_id"):
        store.write(value, BytesIO(b"x"), max_bytes=1,
                    kek=KekRef("kms/test", "v1"), fence=new_fence())


def symlink_or_skip(source, target, *, target_is_directory=False):
    try:
        os.symlink(source, target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")


def test_symlink_root_and_shard_are_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    symlink_or_skip(actual, linked, target_is_directory=True)
    with pytest.raises(StagingSecurityError):
        FilesystemStagingStorage(linked, Keys())

    root = tmp_path / "private"
    store = FilesystemStagingStorage(root, Keys())
    object_id = "ab" + "0" * 30
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_or_skip(outside, root / "ab", target_is_directory=True)
    with pytest.raises(StagingSecurityError):
        write(store, b"x", object_id=object_id)


def test_replaced_root_identity_is_rejected(tmp_path):
    root = tmp_path / "private"
    store = FilesystemStagingStorage(root, Keys())
    displaced = tmp_path / "displaced"
    root.rename(displaced)
    root.mkdir()
    with pytest.raises(StagingSecurityError, match="unsafe_storage_root"):
        write(store, b"x")


def test_symlink_final_and_partial_are_rejected(store, tmp_path):
    object_id, fence = "cd" + "0" * 30, new_fence()
    shard = store._root / "cd"
    shard.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    final = shard / f"{object_id}.enc"
    symlink_or_skip(outside, final)
    with pytest.raises(StagingSecurityError):
        store.delete(object_id)
    final.unlink()
    partial = shard / f"{object_id}.partial.{fence}"
    symlink_or_skip(outside, partial)
    with pytest.raises(StagingSecurityError):
        write(store, b"x", object_id=object_id, fence=fence)
    with pytest.raises(StagingSecurityError):
        store.cleanup_partials(object_id, eligible_fences={fence}, active_fences=set())


def test_hardlink_attack_blocks_read_and_delete(store, tmp_path):
    descriptor = write(store, b"hardlink")
    linked = tmp_path / "linked.enc"
    try:
        os.link(path_for(store, descriptor), linked)
    except OSError:
        pytest.skip("hardlinks are unavailable")
    with pytest.raises(StagingSecurityError):
        read(store, descriptor)
    with pytest.raises(StagingSecurityError):
        store.delete(descriptor.object_id)


def test_hardlinked_partial_is_not_cleaned_as_an_owned_file(store, tmp_path):
    object_id, fence = "de" + "0" * 30, new_fence()
    shard = store._root / "de"
    shard.mkdir()
    partial = shard / f"{object_id}.partial.{fence}"
    partial.write_bytes(b"ciphertext-only")
    linked = tmp_path / "partial-link"
    try:
        os.link(partial, linked)
    except OSError:
        pytest.skip("hardlinks are unavailable")
    with pytest.raises(StagingSecurityError):
        store.cleanup_partials(object_id, eligible_fences={fence}, active_fences=set())
    assert partial.exists() and linked.exists()


def test_missing_shard_uses_stable_content_free_staging_error(store):
    object_id = "aa" + "0" * 30
    kek = KekRef("kms/test", "v1")
    from app.staging.crypto import wrap_dek
    from app.staging.contracts import StagingDescriptor
    descriptor = StagingDescriptor(
        object_id=object_id, format_version=2, chunk_size=8, kek=kek,
        wrapped_dek=wrap_dek(b"d" * 32, object_id=object_id, kek=kek, resolver=store._resolver),
    )
    with pytest.raises(StagingIOError, match="object_unavailable") as caught:
        read(store, descriptor)
    assert str(store._root) not in repr(caught.value)


def test_concurrent_writers_publish_at_most_one_object(tmp_path):
    store = FilesystemStagingStorage(tmp_path / "private", Keys(), chunk_size=8)
    object_id = new_object_id()
    barrier = Barrier(2)
    lock = Lock()
    successes, failures = [], []

    class Racing(BytesIO):
        def read(self, amount):
            if self.tell() == 0:
                barrier.wait(timeout=5)
            return super().read(amount)

    def run(value):
        try:
            result = store.write(object_id, Racing(value), max_bytes=len(value),
                                 kek=KekRef("kms/test", "v1"), fence=new_fence())
            with lock:
                successes.append((result, value))
        except StagingConflict as error:
            with lock:
                failures.append(error)

    threads = [Thread(target=run, args=(value,)) for value in (b"first", b"second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) == len(failures) == 1
    assert read(store, successes[0][0]) == successes[0][1]


def test_cleanup_is_exact_and_preserves_active_and_foreign_partials(store):
    object_id, foreign_id = "ef" + "0" * 30, "ef" + "1" * 30
    stale, active, foreign_fence = new_fence(), new_fence(), new_fence()
    shard = store._root / "ef"
    shard.mkdir()
    if os.name == "posix":
        shard.chmod(0o700)
    stale_path = shard / f"{object_id}.partial.{stale}"
    active_path = shard / f"{object_id}.partial.{active}"
    foreign_path = shard / f"{foreign_id}.partial.{foreign_fence}"
    for path in (stale_path, active_path, foreign_path):
        path.write_bytes(b"ciphertext-only")
    assert store.cleanup_partials(
        object_id, eligible_fences={stale, active}, active_fences={active},
    ) == 1
    assert not stale_path.exists() and active_path.exists() and foreign_path.exists()


def test_delete_is_idempotent_but_io_error_is_not_success(store, monkeypatch):
    descriptor = write(store, b"delete")
    final_name = f"{descriptor.object_id}.enc"
    original = store._unlink_name
    def fail(shard, name):
        if name == final_name:
            raise PermissionError
        return original(shard, name)
    monkeypatch.setattr(store, "_unlink_name", fail)
    with pytest.raises(StagingIOError, match="delete_failed"):
        store.delete(descriptor.object_id)
    monkeypatch.setattr(store, "_unlink_name", original)
    store.delete(descriptor.object_id)
    store.delete(descriptor.object_id)


def test_errors_repr_and_logs_do_not_leak_content_key_or_full_path(tmp_path, caplog):
    secret_content = "SECRET-CONTENT-NEVER-LOG"
    secret_key = "SECRET-KEY-NEVER-LOG"
    secret_root = tmp_path / "SECRET-ROOT-NEVER-LOG"
    class BrokenKeys:
        def resolve(self, reference, version):
            raise RuntimeError(secret_key)
    store = FilesystemStagingStorage(secret_root, BrokenKeys(), chunk_size=8)
    with pytest.raises(StagingIntegrityError) as caught:
        store.write(new_object_id(), BytesIO(secret_content.encode()), max_bytes=100,
                    kek=KekRef("kms/test", "v1"), fence=new_fence())
    rendered = repr(caught.value) + caplog.text + repr(store)
    assert secret_content not in rendered and secret_key not in rendered and str(secret_root) not in rendered


def test_untrusted_source_exception_is_sanitized(store):
    secret = "SOURCE-SECRET-NEVER-LEAK"

    class BrokenSource:
        def read(self, amount):
            raise StagingSecurityError(secret)

    with pytest.raises(StagingIOError, match="plaintext_stream_failed") as caught:
        store.write(
            new_object_id(), BrokenSource(), max_bytes=10,
            kek=KekRef("kms/test", "v1"), fence=new_fence(),
        )
    assert secret not in repr(caught.value)


def test_private_permissions_and_runtime_protocol(store):
    assert isinstance(store, StagingStorage)
    if os.name == "posix":
        assert stat.S_IMODE(store._root.stat().st_mode) == 0o700
        descriptor = write(store, b"mode")
        assert stat.S_IMODE(path_for(store, descriptor).stat().st_mode) == 0o600
