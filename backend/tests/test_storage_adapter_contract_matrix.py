from app.integrations.contracts import StorageAdapter
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.organizer_engine.drive import DriveClient


def test_google_and_yandex_implement_the_same_core_contract():
    required = {"health", "get_object", "list_children", "walk_tree", "read_bytes", "copy_folder_tree"}
    mutable = {"get_file_meta", "create_folder", "assert_inside_copy", "rename_file", "move_file", "trash_safe_copy"}
    for adapter_type in (DriveClient, YandexDiskStorageAdapter):
        assert required.issubset(set(dir(adapter_type))), adapter_type.__name__
        assert mutable.issubset(set(dir(adapter_type))), adapter_type.__name__


def test_core_contract_has_no_provider_specific_method_names():
    names = set(dir(StorageAdapter))
    assert not any("google" in name or "yandex" in name for name in names)


import pytest

from app.integrations.contracts import StorageQuotaExceeded
from app.integrations.pu_storage import PuStorageAdapter, RdVersionContentStore


SIX = b"abcdef"
TWO = b"ab"


def _adapter(tmp_path, project_id=1, quota=10):
    return PuStorageAdapter(project_id=project_id, root=tmp_path, quota_bytes=quota)


def _store(tmp_path, quota=10):
    return RdVersionContentStore(root=tmp_path, quota_bytes=quota)


def _mutable_files(adapter):
    root = adapter._ensure_project_root()
    return sorted(p.name for p in root.iterdir() if p.is_file())


def test_cas_then_mutable_write_rejects_and_preserves_cas(tmp_path):
    adapter = _adapter(tmp_path)
    store = _store(tmp_path)
    digest = store.put(1, SIX)
    before = store.read(1, digest, len(SIX))
    with pytest.raises(StorageQuotaExceeded):
        adapter.write_file(None, "mutable.bin", SIX)
    assert store.read(1, digest, len(SIX)) == before
    assert _mutable_files(adapter) == []


def test_mutable_then_cas_rejects_and_preserves_mutable(tmp_path):
    adapter = _adapter(tmp_path)
    store = _store(tmp_path)
    file_id = adapter.write_file(None, "mutable.bin", SIX)
    before, _ = adapter.read_bytes(file_id, len(SIX))
    with pytest.raises(StorageQuotaExceeded):
        store.put(1, SIX)
    after, _ = adapter.read_bytes(file_id, len(SIX))
    assert after == before
    project_dir = store._project_dir(1, create=False)
    assert not any(p.name.startswith(".tmp-") for p in project_dir.iterdir())


def test_copy_folder_tree_rejects_under_quota_and_leaves_source(tmp_path):
    adapter = _adapter(tmp_path)
    folder_id = adapter.create_folder("src")
    adapter.write_file(folder_id, "a.bin", SIX)
    before = adapter.walk_tree(folder_id)
    with pytest.raises(StorageQuotaExceeded):
        adapter.copy_folder_tree(folder_id, "pu:/", "src")
    assert adapter.walk_tree(folder_id) == before
    root = adapter._ensure_project_root()
    assert sorted(p.name for p in root.iterdir()) == ["src"]


def test_project_budgets_are_independent(tmp_path):
    a1 = _adapter(tmp_path, project_id=1, quota=10)
    a2 = _adapter(tmp_path, project_id=2, quota=10)
    a1.write_file(None, "one.bin", TWO)
    a2.write_file(None, "two.bin", TWO)
    assert a1.usage() == (2, 10)
    assert a2.usage() == (2, 10)


def test_cas_dedup_does_not_double_usage_and_usage_includes_retained_cas(tmp_path):
    adapter = _adapter(tmp_path)
    store = _store(tmp_path)
    store.put(1, SIX)
    store.put(1, SIX)
    assert store._used_bytes(1) == len(SIX)
    used, quota = adapter.usage()
    assert quota == 10
    assert used == len(SIX)



def test_mutable_temporary_named_file_counts_toward_quota(tmp_path):
    adapter=_adapter(tmp_path)
    adapter.write_file(None,'.tmp-user.bin',SIX)
    assert adapter.usage()==(6,10)
    with pytest.raises(StorageQuotaExceeded):adapter.write_file(None,'second.bin',SIX)


def _mixed_quota_worker(root, kind, ready, start, queue):
    try:
        adapter = PuStorageAdapter(project_id=1, root=root, quota_bytes=10)
        store = RdVersionContentStore(root=root, quota_bytes=10)
        ready.set()
        if not start.wait(5):
            raise RuntimeError("start timeout")
        try:
            if kind == 'mutable':
                adapter.write_file(None, 'mixed.bin', b'abcdef')
            else:
                store.put(1, b'uvwxyz')
            queue.put((kind, 'ok'))
        except StorageQuotaExceeded:
            queue.put((kind, 'quota'))
    except BaseException as exc:
        queue.put((kind, type(exc).__name__))


@pytest.mark.parametrize('attempt', range(3))
def test_mixed_writers_share_quota(tmp_path, attempt):
    import multiprocessing
    ctx = multiprocessing.get_context('fork')
    queue, start = ctx.Queue(), ctx.Event()
    ready = [ctx.Event(), ctx.Event()]
    workers = [ctx.Process(target=_mixed_quota_worker,
               args=(tmp_path, kind, ready[i], start, queue))
               for i, kind in enumerate(('mutable', 'cas'))]
    try:
        for worker in workers:
            worker.start()
        assert all(event.wait(5) for event in ready)
        start.set()
        for worker in workers:
            worker.join(5)
        assert all(not worker.is_alive() and worker.exitcode == 0 for worker in workers)
        results = [queue.get(timeout=1) for _ in workers]
        assert sorted(status for _, status in results) == ['ok', 'quota'], results
        assert _adapter(tmp_path).usage() == (6, 10)
        files = [path for path in tmp_path.rglob('*') if path.is_file() and not path.name.startswith('.')]
        assert len(files) == 1 and files[0].read_bytes() in (b'abcdef', b'uvwxyz')
        assert not any(path.name.startswith('.tmp-') for path in tmp_path.rglob('*'))
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(2)
        queue.close()
        queue.join_thread()


def test_copy_failure_removes_partial_tree_and_allows_retry(tmp_path, monkeypatch):
    import shutil
    from app.integrations.contracts import StorageUnavailable
    adapter = _adapter(tmp_path, quota=20)
    folder = adapter.create_folder('source')
    source_file = adapter.write_file(folder, 'a.bin', SIX)
    import os
    from app.integrations import pu_storage
    original_copy = pu_storage._copy_tree_fds
    def fail_after_partial_copy(source_fd, destination_fd, byte_budget):
        fd = os.open('partial.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=destination_fd)
        try:
            os.write(fd, b'abc')
        finally:
            os.close(fd)
        raise OSError('synthetic copy failure')
    with monkeypatch.context() as patch:
        patch.setattr(pu_storage, '_copy_tree_fds', fail_after_partial_copy)
        with pytest.raises(StorageUnavailable):
            adapter.copy_folder_tree(folder, 'pu:/', 'source')
    assert pu_storage._copy_tree_fds is original_copy
    assert adapter.read_bytes(source_file, 6)[0] == SIX
    assert adapter.usage() == (6, 20)
    assert sorted(p.name for p in adapter._ensure_project_root().iterdir()) == ['source']
    result = adapter.copy_folder_tree(folder, 'pu:/', 'source')
    assert adapter.read_bytes(result.copy_root_id + '/a.bin', 6)[0] == SIX
    assert adapter.usage() == (12, 20)


@pytest.mark.parametrize('operation', ['rename', 'move', 'trash'])
def test_mutations_hold_shared_project_lock(tmp_path, monkeypatch, operation):
    import fcntl
    import shutil
    from pathlib import Path
    adapter = _adapter(tmp_path, quota=20)
    copy = adapter.create_folder('copy')
    target = adapter.create_folder('target', copy)
    file_id = adapter.write_file(copy, 'a.bin', SIX)
    checks = []
    def verify_kernel_lock():
        with (tmp_path / '.rd-version-content' / '.lock-1').open('r+b') as same:
            with pytest.raises(BlockingIOError):
                fcntl.flock(same.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (tmp_path / '.rd-version-content' / '.lock-2').open('a+b') as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other.fileno(), fcntl.LOCK_UN)
        checks.append(operation)
    import os
    from app.integrations import pu_storage
    original_rename, original_move = pu_storage._rename_noreplace, shutil.move
    def rename_hook(source, destination, **kwargs):
        verify_kernel_lock()
        return original_rename(source, destination, **kwargs)
    def move_hook(source, destination):
        verify_kernel_lock()
        return original_move(source, destination)
    with monkeypatch.context() as patch:
        if operation in ('rename', 'move'):
            patch.setattr(pu_storage, '_rename_noreplace', rename_hook)
            if operation == 'rename':
                adapter.rename_file(file_id, 'renamed.bin', copy)
            else:
                adapter.move_file(file_id, target, copy, copy)
        else:
            patch.setattr(pu_storage, '_rename_noreplace', rename_hook)
            adapter.trash_safe_copy(copy)
    assert checks == [operation]
    assert adapter.usage() == (0 if operation == 'trash' else 6, 20)
    # The lock must also be released after the operation, permitting the next writer.
    with (tmp_path / '.rd-version-content' / '.lock-1').open('r+b') as released:
        fcntl.flock(released.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(released.fileno(), fcntl.LOCK_UN)


def test_mutable_write_parent_swap_never_writes_other_project(tmp_path, monkeypatch):
    from app.integrations.contracts import StorageAccessDenied, StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    parent = adapter.create_folder('parent')
    foreign = tmp_path / '2'
    foreign.mkdir()
    victim = foreign / 'a.bin'
    victim.write_bytes(b'FOREIGN_UNCHANGED')
    original_usage = adapter._used_bytes
    swapped = []
    def swap_after_usage():
        result = original_usage()
        (tmp_path / '1' / 'parent').rename(tmp_path / '1' / 'held-parent')
        (tmp_path / '1' / 'parent').symlink_to(foreign, target_is_directory=True)
        swapped.append(True)
        return result
    monkeypatch.setattr(adapter, '_used_bytes', swap_after_usage)
    try:
        adapter.write_file(parent, 'a.bin', b'OWN_PROJECT')
    except (StorageAccessDenied, StorageUnavailable, FileNotFoundError):
        pass
    assert swapped == [True]
    assert victim.read_bytes() == b'FOREIGN_UNCHANGED'
    assert sorted(p.name for p in foreign.iterdir()) == ['a.bin']


def test_mutable_write_held_parent_survives_swap_at_create(tmp_path, monkeypatch):
    import os
    adapter = _adapter(tmp_path, quota=100)
    parent = adapter.create_folder('parent')
    foreign = tmp_path / '2'
    foreign.mkdir()
    original_open = os.open
    swapped = []
    def swap_at_create(path, flags, mode=0o777, *, dir_fd=None):
        if path == 'a.bin' and flags & os.O_CREAT:
            assert dir_fd is not None
            (tmp_path / '1' / 'parent').rename(tmp_path / '1' / 'held-parent')
            (tmp_path / '1' / 'parent').symlink_to(foreign, target_is_directory=True)
            swapped.append(True)
        return original_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, 'open', swap_at_create)
    adapter.write_file(parent, 'a.bin', b'OWN_PROJECT')
    assert swapped == [True]
    assert list(foreign.iterdir()) == []
    assert (tmp_path / '1' / 'held-parent' / 'a.bin').read_bytes() == b'OWN_PROJECT'


@pytest.mark.parametrize('swap_kind', ['parent', 'leaf'])
def test_mutable_read_swap_never_exposes_foreign_bytes(tmp_path, monkeypatch, swap_kind):
    import os
    from pathlib import Path
    from app.integrations.contracts import StorageAccessDenied, StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    parent = adapter.create_folder('parent')
    file_id = adapter.write_file(parent, 'a.bin', b'OWN')
    foreign = tmp_path / '2'
    foreign.mkdir()
    (foreign / 'a.bin').write_bytes(b'FOREIGN_SECRET')
    original_open, original_read = os.open, Path.read_bytes
    swaps = []
    def swap():
        if swaps:
            return
        if swap_kind == 'parent':
            (tmp_path / '1' / 'parent').rename(tmp_path / '1' / 'held-parent')
            (tmp_path / '1' / 'parent').symlink_to(foreign, target_is_directory=True)
        else:
            (tmp_path / '1' / 'parent' / 'a.bin').unlink()
            (tmp_path / '1' / 'parent' / 'a.bin').symlink_to(foreign / 'a.bin')
        swaps.append(swap_kind)
    def read_hook(path):
        swap()
        return original_read(path)
    def open_hook(path, flags, mode=0o777, *, dir_fd=None):
        if path == 'a.bin' and not flags & os.O_CREAT:
            swap()
        return original_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(Path, 'read_bytes', read_hook)
    monkeypatch.setattr(os, 'open', open_hook)
    try:
        result, _ = adapter.read_bytes(file_id, 64)
    except (StorageAccessDenied, StorageUnavailable, FileNotFoundError):
        result = None
    assert swaps == [swap_kind]
    assert result in (None, b'OWN')


def test_mutable_read_growth_after_fstat_is_bounded(tmp_path, monkeypatch):
    import os
    import stat
    adapter = _adapter(tmp_path, quota=100)
    file_id = adapter.write_file(None, 'growth.bin', b'OWN')
    original = os.fstat
    grew = []
    def grow_after_stat(fd):
        info = original(fd)
        if stat.S_ISREG(info.st_mode) and not grew:
            (tmp_path / '1' / 'growth.bin').write_bytes(b'X' * 32)
            grew.append(True)
        return info
    monkeypatch.setattr(os, 'fstat', grow_after_stat)
    with pytest.raises(ValueError, match='exceeds 4 bytes'):
        adapter.read_bytes(file_id, 4)
    assert grew == [True]


@pytest.mark.parametrize('budget', [-1, True, '5', None, 1.5])
def test_mutable_read_invalid_limit(tmp_path, budget):
    adapter = _adapter(tmp_path)
    file_id = adapter.write_file(None, 'a.bin', b'a')
    with pytest.raises(ValueError, match='Invalid storage read limit'):
        adapter.read_bytes(file_id, budget)


def test_mutable_read_zero_limit_only_accepts_empty(tmp_path):
    adapter = _adapter(tmp_path)
    empty = adapter.write_file(None, 'empty.bin', b'')
    full = adapter.write_file(None, 'full.bin', b'a')
    assert adapter.read_bytes(empty, 0)[0] == b''
    with pytest.raises(ValueError):
        adapter.read_bytes(full, 0)


def _mutable_fifo_read_worker(root, queue):
    from app.integrations.contracts import StorageAccessDenied
    try:
        PuStorageAdapter(1, root=root, quota_bytes=100).read_bytes('pu:/fifo', 10)
        queue.put('unexpected-success')
    except StorageAccessDenied:
        queue.put('denied')
    except BaseException as exc:
        queue.put(type(exc).__name__)


def test_mutable_fifo_read_never_blocks(tmp_path):
    import os
    import multiprocessing
    adapter = _adapter(tmp_path)
    os.mkfifo(adapter._ensure_project_root() / 'fifo')
    ctx = multiprocessing.get_context('fork')
    queue = ctx.Queue()
    worker = ctx.Process(target=_mutable_fifo_read_worker, args=(tmp_path, queue))
    try:
        worker.start()
        worker.join(2)
        assert not worker.is_alive(), 'Mutable FIFO read blocked in kernel'
        assert worker.exitcode == 0
        assert queue.get(timeout=1) == 'denied'
    finally:
        if worker.is_alive():
            worker.terminate()
        if worker.pid is not None:
            worker.join(2)
        queue.close()
        queue.join_thread()


@pytest.mark.parametrize('operation', ['read', 'write'])
def test_mutable_fdopen_failure_does_not_leak(tmp_path, monkeypatch, operation):
    import os
    from app.integrations.contracts import StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    file_id = adapter.write_file(None, 'existing.bin', b'OWN')
    before = len(os.listdir('/proc/self/fd'))
    def fail_fdopen(*args, **kwargs):
        raise OSError('synthetic fdopen failure')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fdopen', fail_fdopen)
        for index in range(8):
            with pytest.raises(StorageUnavailable):
                if operation == 'read':
                    adapter.read_bytes(file_id, 10)
                else:
                    adapter.write_file(None, f'failed-{index}.bin', b'X')
    assert len(os.listdir('/proc/self/fd')) == before
    assert sorted(p.name for p in adapter._ensure_project_root().iterdir()) == ['existing.bin']
    assert adapter.read_bytes(file_id, 3)[0] == b'OWN'


def test_create_folder_parent_swap_never_creates_foreign_directory(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from app.integrations.contracts import StorageAccessDenied, StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    parent = adapter.create_folder('parent')
    foreign = tmp_path / '2'
    foreign.mkdir()
    original_path_mkdir, original_mkdir = Path.mkdir, os.mkdir
    swaps = []
    def swap():
        if not swaps:
            (tmp_path / '1' / 'parent').rename(tmp_path / '1' / 'held-parent')
            (tmp_path / '1' / 'parent').symlink_to(foreign, target_is_directory=True)
            swaps.append(True)
    def path_hook(path, *args, **kwargs):
        if path.name == 'new':
            swap()
        return original_path_mkdir(path, *args, **kwargs)
    def mkdir_hook(path, mode=0o777, *, dir_fd=None):
        if path == 'new':
            assert dir_fd is not None
            swap()
        return original_mkdir(path, mode, dir_fd=dir_fd)
    monkeypatch.setattr(Path, 'mkdir', path_hook)
    monkeypatch.setattr(os, 'mkdir', mkdir_hook)
    try:
        adapter.create_folder('new', parent)
    except (StorageAccessDenied, StorageUnavailable, FileNotFoundError):
        pass
    assert swaps == [True]
    assert list(foreign.iterdir()) == []


def test_create_folder_existing_directory_is_idempotent_and_symlink_denied(tmp_path):
    from app.integrations.contracts import StorageAccessDenied, StorageUnavailable
    adapter = _adapter(tmp_path)
    original = adapter.create_folder('original')
    assert adapter.create_folder('original') == original
    # Even an in-project alias must not be traversed as a creation target.
    (tmp_path / '1' / 'alias').symlink_to(tmp_path / '1' / 'original', target_is_directory=True)
    with pytest.raises((StorageAccessDenied, StorageUnavailable)):
        adapter.create_folder('alias')


def test_rename_parent_swap_never_modifies_foreign_project(tmp_path, monkeypatch):
    import os
    adapter = _adapter(tmp_path, quota=100)
    copy = adapter.create_folder('copy')
    file_id = adapter.write_file(copy, 'a.bin', b'OWN')
    foreign = tmp_path / '2'
    foreign.mkdir()
    (foreign / 'a.bin').write_bytes(b'FOREIGN')
    original_rename = os.rename
    from app.integrations import pu_storage
    original_noreplace = pu_storage._rename_noreplace
    swapped = []
    def rename_hook(source, destination, **kwargs):
        if not swapped:
            original_rename(tmp_path / '1' / 'copy', tmp_path / '1' / 'held-copy')
            (tmp_path / '1' / 'copy').symlink_to(foreign, target_is_directory=True)
            swapped.append(True)
        return original_noreplace(source, destination, **kwargs)
    monkeypatch.setattr(pu_storage, '_rename_noreplace', rename_hook)
    adapter.rename_file(file_id, 'new.bin', copy)
    assert swapped == [True]
    assert sorted(p.name for p in foreign.iterdir()) == ['a.bin']
    assert (foreign / 'a.bin').read_bytes() == b'FOREIGN'


def test_rename_existing_destination_never_overwrites(tmp_path):
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    copy = adapter.create_folder('copy')
    source = adapter.write_file(copy, 'a.bin', b'A')
    target = adapter.write_file(copy, 'b.bin', b'B')
    with pytest.raises(StorageAccessDenied):
        adapter.rename_file(source, 'b.bin', copy)
    assert adapter.read_bytes(source, 1)[0] == b'A'
    assert adapter.read_bytes(target, 1)[0] == b'B'
    adapter.rename_file(source, 'a.bin', copy)
    assert adapter.read_bytes(source, 1)[0] == b'A'


def test_rename_racing_destination_is_never_overwritten(tmp_path, monkeypatch):
    import os
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    copy = adapter.create_folder('copy')
    source = adapter.write_file(copy, 'a.bin', b'OWN')
    destination = tmp_path / '1' / 'copy' / 'new.bin'
    original_rename = os.rename
    noreplace = getattr(pu_storage, '_rename_noreplace', None)
    injected = []
    def inject():
        destination.write_bytes(b'RACING_FILE')
        injected.append(True)
    def legacy_hook(*args, **kwargs):
        inject()
        return original_rename(*args, **kwargs)
    def protected_hook(*args, **kwargs):
        inject()
        return noreplace(*args, **kwargs)
    if noreplace is None:
        monkeypatch.setattr(os, 'rename', legacy_hook)
    else:
        monkeypatch.setattr(pu_storage, '_rename_noreplace', protected_hook)
    with pytest.raises(StorageAccessDenied):
        adapter.rename_file(source, 'new.bin', copy)
    assert injected == [True]
    assert destination.read_bytes() == b'RACING_FILE'
    assert adapter.read_bytes(source, 3)[0] == b'OWN'


@pytest.mark.parametrize('failure', ['missing', 'ENOSYS', 'EINVAL', 'EOPNOTSUPP'])
def test_atomic_rename_unavailable_never_falls_back(tmp_path, monkeypatch, failure):
    import ctypes
    import errno
    import os
    from app.integrations.contracts import StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    copy = adapter.create_folder('copy')
    source = adapter.write_file(copy, 'a.bin', b'OWN')
    class FailedCall:
        def __call__(self, *args):
            ctypes.set_errno(getattr(errno, failure))
            return -1
    class FakeLibrary:
        pass
    library = FakeLibrary()
    if failure != 'missing':
        library.renameat2 = FailedCall()
    monkeypatch.setattr(ctypes, 'CDLL', lambda *args, **kwargs: library)
    def forbid_fallback(*args, **kwargs):
        pytest.fail('Non-atomic rename fallback must never execute')
    monkeypatch.setattr(os, 'rename', forbid_fallback)
    with pytest.raises(StorageUnavailable, match='Atomic no-replace rename is unavailable'):
        adapter.rename_file(source, 'new.bin', copy)
    assert adapter.read_bytes(source, 3)[0] == b'OWN'
    assert not (tmp_path / '1' / 'copy' / 'new.bin').exists()


def test_atomic_rename_keeps_directory_support(tmp_path):
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path)
    copy = adapter.create_folder('copy')
    source = adapter.create_folder('source', copy)
    adapter.write_file(source, 'a.bin', b'A')
    adapter.create_folder('existing', copy)
    with pytest.raises(StorageAccessDenied):
        adapter.rename_file(source, 'existing', copy)
    adapter.rename_file(source, 'renamed', copy)
    assert adapter.read_bytes(copy + '/renamed/a.bin', 1)[0] == b'A'


@pytest.mark.parametrize('swapped_parent', ['source', 'destination'])
def test_move_parent_swaps_never_modify_foreign_project(tmp_path, monkeypatch, swapped_parent):
    import os
    from app.integrations import pu_storage
    adapter = _adapter(tmp_path, quota=100)
    copy = adapter.create_folder('copy')
    src = adapter.create_folder('src', copy)
    dst = adapter.create_folder('dst', copy)
    source = adapter.write_file(src, 'a.bin', b'OWN')
    foreign = tmp_path / '2'
    foreign.mkdir()
    if swapped_parent == 'source':
        (foreign / 'a.bin').write_bytes(b'FOREIGN')
    original_rename, original_noreplace = os.rename, pu_storage._rename_noreplace
    swaps = []
    def swap():
        if not swaps:
            name = 'src' if swapped_parent == 'source' else 'dst'
            original_rename(tmp_path / '1' / 'copy' / name, tmp_path / '1' / 'copy' / ('held-' + name))
            (tmp_path / '1' / 'copy' / name).symlink_to(foreign, target_is_directory=True)
            swaps.append(True)
    def legacy_hook(*args, **kwargs):
        swap()
        return original_rename(*args, **kwargs)
    def protected_hook(*args, **kwargs):
        swap()
        return original_noreplace(*args, **kwargs)
    monkeypatch.setattr(os, 'rename', legacy_hook)
    monkeypatch.setattr(pu_storage, '_rename_noreplace', protected_hook)
    adapter.move_file(source, dst, src, copy)
    assert swaps == [True]
    if swapped_parent == 'source':
        assert (foreign / 'a.bin').read_bytes() == b'FOREIGN'
        assert (tmp_path / '1' / 'copy' / 'dst' / 'a.bin').read_bytes() == b'OWN'
    else:
        assert list(foreign.iterdir()) == []
        assert (tmp_path / '1' / 'copy' / 'held-dst' / 'a.bin').read_bytes() == b'OWN'


def test_move_racing_destination_preserves_both_files(tmp_path, monkeypatch):
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    copy = adapter.create_folder('copy')
    src = adapter.create_folder('src', copy)
    dst = adapter.create_folder('dst', copy)
    source = adapter.write_file(src, 'a.bin', b'OWN')
    original = pu_storage._rename_noreplace
    injections = []
    def inject(*args, **kwargs):
        (tmp_path / '1' / 'copy' / 'dst' / 'a.bin').write_bytes(b'RACING')
        injections.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(pu_storage, '_rename_noreplace', inject)
    with pytest.raises(StorageAccessDenied):
        adapter.move_file(source, dst, src, copy)
    assert injections == [True]
    assert adapter.read_bytes(source, 3)[0] == b'OWN'
    assert adapter.read_bytes(dst + '/a.bin', 6)[0] == b'RACING'


def test_move_cross_device_error_never_falls_back_to_copy(tmp_path, monkeypatch):
    import errno
    import shutil
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    copy = adapter.create_folder('copy')
    src = adapter.create_folder('src', copy)
    dst = adapter.create_folder('dst', copy)
    source = adapter.write_file(src, 'a.bin', b'OWN')
    def unavailable(*args, **kwargs):
        raise OSError(errno.EXDEV, 'synthetic cross-device failure')
    def forbid_copy(*args, **kwargs):
        pytest.fail('Path-based move fallback must never execute')
    monkeypatch.setattr(pu_storage, '_rename_noreplace', unavailable)
    monkeypatch.setattr(shutil, 'move', forbid_copy)
    with pytest.raises(StorageUnavailable):
        adapter.move_file(source, dst, src, copy)
    assert adapter.read_bytes(source, 3)[0] == b'OWN'
    assert list((tmp_path / '1' / 'copy' / 'dst').iterdir()) == []


@pytest.mark.parametrize('swap_kind', ['source_parent', 'trash_parent'])
def test_trash_parent_swaps_never_modify_foreign_project(tmp_path, monkeypatch, swap_kind):
    import os
    from app.integrations import pu_storage
    adapter = _adapter(tmp_path, quota=100)
    parent = adapter.create_folder('parent')
    copy = adapter.create_folder('copy', parent)
    adapter.write_file(copy, 'own.bin', b'OWN')
    foreign = tmp_path / '2'
    foreign.mkdir()
    if swap_kind == 'source_parent':
        (foreign / 'copy').mkdir()
        (foreign / 'copy' / 'secret.bin').write_bytes(b'FOREIGN')
    original_rename, original_noreplace = os.rename, pu_storage._rename_noreplace
    swaps = []
    def swap():
        if not swaps:
            name = 'parent' if swap_kind == 'source_parent' else '.trash'
            original_rename(tmp_path / '1' / name, tmp_path / '1' / ('held-' + name))
            (tmp_path / '1' / name).symlink_to(foreign, target_is_directory=True)
            swaps.append(True)
    def legacy_hook(*args, **kwargs):
        swap()
        return original_rename(*args, **kwargs)
    def protected_hook(*args, **kwargs):
        swap()
        return original_noreplace(*args, **kwargs)
    monkeypatch.setattr(os, 'rename', legacy_hook)
    monkeypatch.setattr(pu_storage, '_rename_noreplace', protected_hook)
    adapter.trash_safe_copy(copy)
    assert swaps == [True]
    if swap_kind == 'source_parent':
        assert (foreign / 'copy' / 'secret.bin').read_bytes() == b'FOREIGN'
        trash = tmp_path / '1' / '.trash'
    else:
        assert list(foreign.iterdir()) == []
        trash = tmp_path / '1' / 'held-.trash'
    entries = list(trash.iterdir())
    assert len(entries) == 1
    assert (entries[0] / 'own.bin').read_bytes() == b'OWN'


def test_trash_rejects_project_root_and_symlink_destination(tmp_path):
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path)
    copy = adapter.create_folder('copy')
    source = adapter.write_file(copy, 'a.bin', b'A')
    with pytest.raises(StorageAccessDenied):
        adapter.trash_safe_copy('pu:/')
    foreign = tmp_path / '2'
    foreign.mkdir()
    (tmp_path / '1' / '.trash').symlink_to(foreign, target_is_directory=True)
    with pytest.raises(StorageAccessDenied):
        adapter.trash_safe_copy(copy)
    assert adapter.read_bytes(source, 1)[0] == b'A'
    assert list(foreign.iterdir()) == []


def test_trash_racing_target_never_overwrites(tmp_path, monkeypatch):
    import os
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path)
    copy = adapter.create_folder('copy')
    source = adapter.write_file(copy, 'a.bin', b'A')
    original = pu_storage._rename_noreplace
    injected = []
    def collide(source_name, target, *, src_dir_fd, dst_dir_fd):
        os.mkdir(target, dir_fd=dst_dir_fd)
        injected.append(target)
        return original(source_name, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    monkeypatch.setattr(pu_storage, '_rename_noreplace', collide)
    with pytest.raises(StorageAccessDenied):
        adapter.trash_safe_copy(copy)
    assert len(injected) == 1
    assert adapter.read_bytes(source, 1)[0] == b'A'
    assert (tmp_path / '1' / '.trash' / injected[0]).is_dir()
    assert list((tmp_path / '1' / '.trash' / injected[0]).iterdir()) == []


@pytest.mark.parametrize('swap_kind', ['source', 'destination'])
def test_copytree_parent_swap_has_no_foreign_side_effects(tmp_path, monkeypatch, swap_kind):
    import os
    from pathlib import Path
    from app.integrations.contracts import StorageAccessDenied, StorageUnavailable
    adapter = _adapter(tmp_path, quota=1000)
    source = adapter.create_folder('source')
    destination = adapter.create_folder('destination')
    adapter.write_file(source, 'own.bin', b'OWN')
    foreign = tmp_path / '2'
    foreign.mkdir()
    if swap_kind == 'source':
        (foreign / 'foreign.bin').write_bytes(b'FOREIGN')
    from app.integrations import pu_storage
    original_publish = pu_storage._rename_noreplace
    swapped = []
    def hook(source_name, target_name, *, src_dir_fd, dst_dir_fd):
        if target_name.startswith('copy — PU safe copy') and not swapped:
            (tmp_path / '1' / swap_kind).rename(tmp_path / '1' / ('held-' + swap_kind))
            (tmp_path / '1' / swap_kind).symlink_to(foreign, target_is_directory=True)
            swapped.append(True)
        return original_publish(source_name, target_name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    monkeypatch.setattr(pu_storage, '_rename_noreplace', hook)
    try:
        adapter.copy_folder_tree(source, destination, 'copy')
    except (StorageAccessDenied, StorageUnavailable, FileNotFoundError):
        pass
    assert swapped == [True]
    if swap_kind == 'source':
        assert (foreign / 'foreign.bin').read_bytes() == b'FOREIGN'
        assert not any(p.name == 'foreign.bin' for p in (tmp_path / '1' / 'destination').rglob('*'))
        assert (tmp_path / '1' / 'held-source' / 'own.bin').read_bytes() == b'OWN'
    else:
        assert list(foreign.iterdir()) == []
        assert (tmp_path / '1' / 'source' / 'own.bin').read_bytes() == b'OWN'


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_anchored_copy_rejects_special_entries_and_cleans_partial(tmp_path, monkeypatch, kind):
    import os
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'a-first.bin', b'OWN')
    foreign = tmp_path / '2'
    foreign.mkdir()
    (foreign / 'secret.bin').write_bytes(b'FOREIGN')
    unsafe = tmp_path / '1' / 'source' / 'z-unsafe'
    original_usage = adapter._used_bytes
    inserted = []
    def insert_after_usage():
        used = original_usage()
        if kind == 'symlink':
            unsafe.symlink_to(foreign / 'secret.bin')
        else:
            os.mkfifo(unsafe)
        inserted.append(True)
        return used
    monkeypatch.setattr(adapter, '_used_bytes', insert_after_usage)
    before = len(os.listdir('/proc/self/fd'))
    with pytest.raises(StorageAccessDenied):
        adapter.copy_folder_tree(source, 'pu:/', 'copy')
    assert inserted == [True]
    assert len(os.listdir('/proc/self/fd')) == before
    assert sorted(p.name for p in (tmp_path / '1').iterdir()) == ['source']
    assert (foreign / 'secret.bin').read_bytes() == b'FOREIGN'


def test_anchored_copy_source_growth_rejects_and_cleans(tmp_path, monkeypatch):
    import os
    from app.integrations.contracts import StorageQuotaExceeded
    adapter = _adapter(tmp_path, quota=10)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'a.bin', b'OWN')
    original = os.read
    grew = []
    def grow_at_read(fd, size):
        if not grew:
            (tmp_path / '1' / 'source' / 'a.bin').write_bytes(b'X' * 32)
            grew.append(True)
        assert size <= 8
        return original(fd, size)
    monkeypatch.setattr(os, 'read', grow_at_read)
    with pytest.raises(StorageQuotaExceeded):
        adapter.copy_folder_tree(source, 'pu:/', 'copy')
    assert grew == [True]
    assert sorted(p.name for p in (tmp_path / '1').iterdir()) == ['source']


def test_anchored_copy_rejects_descendant_destination(tmp_path):
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path)
    source = adapter.create_folder('source')
    child = adapter.create_folder('child', source)
    with pytest.raises(StorageAccessDenied):
        adapter.copy_folder_tree(source, child, 'copy')
    assert list((tmp_path / '1' / 'source' / 'child').iterdir()) == []


def test_copy_source_swap_after_usage_denied_before_copy(tmp_path, monkeypatch):
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    destination = adapter.create_folder('destination')
    adapter.write_file(source, 'own.bin', b'OWN')
    foreign = tmp_path / '2'
    foreign.mkdir()
    (foreign / 'foreign.bin').write_bytes(b'FOREIGN')
    original_usage = adapter._used_bytes
    swaps = []
    def swap_after_usage():
        used = original_usage()
        (tmp_path / '1' / 'source').rename(tmp_path / '1' / 'held-source')
        (tmp_path / '1' / 'source').symlink_to(foreign, target_is_directory=True)
        swaps.append(True)
        return used
    monkeypatch.setattr(adapter, '_used_bytes', swap_after_usage)
    with pytest.raises(StorageAccessDenied):
        adapter.copy_folder_tree(source, destination, 'copy')
    assert swaps == [True]
    assert list((tmp_path / '1' / 'destination').iterdir()) == []
    assert (foreign / 'foreign.bin').read_bytes() == b'FOREIGN'
    assert (tmp_path / '1' / 'held-source' / 'own.bin').read_bytes() == b'OWN'


def test_copy_complete_tree_is_published_only_after_copy(tmp_path, monkeypatch):
    from app.integrations import pu_storage
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    destination = adapter.create_folder('destination')
    adapter.write_file(source, 'own.bin', b'OWN')
    original = pu_storage._copy_tree_fds
    checks = []
    def inspect(source_fd, target_fd, budget):
        assert adapter.list_children(destination) == []
        assert sorted(item.name for item in adapter.list_children('pu:/')) == ['destination', 'source']
        paths = original(source_fd, target_fd, budget)
        assert adapter.list_children(destination) == []
        checks.append(True)
        return paths
    monkeypatch.setattr(pu_storage, '_copy_tree_fds', inspect)
    result = adapter.copy_folder_tree(source, destination, 'copy')
    assert checks == [True]
    assert [item.id for item in adapter.list_children(destination)] == [result.copy_root_id]
    assert adapter.read_bytes(result.copy_root_id + '/own.bin', 3)[0] == b'OWN'
    assert not (tmp_path / '1' / '.pu-copy-staging').exists()


def test_copy_staging_bytes_count_and_crash_leftovers_are_retained(tmp_path):
    from app.integrations.contracts import StorageQuotaExceeded, StorageAccessDenied
    adapter = _adapter(tmp_path, quota=10)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    stale = tmp_path / '1' / '.pu-copy-staging' / 'old-crash'
    stale.mkdir(parents=True)
    (stale / 'retained.bin').write_bytes(b'123456')
    assert adapter.usage() == (9, 10)
    assert [item.name for item in adapter.list_children('pu:/')] == ['source']
    with pytest.raises(StorageAccessDenied):
        adapter.read_bytes('pu:/.pu-copy-staging/old-crash/retained.bin', 6)
    with pytest.raises(StorageQuotaExceeded):
        adapter.copy_folder_tree(source, 'pu:/', 'copy')
    assert (stale / 'retained.bin').read_bytes() == b'123456'
    assert list(stale.parent.iterdir()) == [stale]


def test_copy_publication_collision_preserves_existing_tree(tmp_path, monkeypatch):
    import os
    from app.integrations import pu_storage
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    dst = adapter.create_folder('destination')
    adapter.write_file(source, 'own.bin', b'OWN')
    original = pu_storage._rename_noreplace
    inserted = []
    def collide(source_name, target_name, *, src_dir_fd, dst_dir_fd):
        if not inserted:
            os.mkdir(target_name, dir_fd=dst_dir_fd)
            inserted.append(target_name)
        return original(source_name, target_name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    monkeypatch.setattr(pu_storage, '_rename_noreplace', collide)
    result = adapter.copy_folder_tree(source, dst, 'copy')
    assert len(inserted) == 1 and result.copy_root_name.endswith('(2)')
    assert list((tmp_path / '1' / 'destination' / inserted[0]).iterdir()) == []
    assert adapter.read_bytes(result.copy_root_id + '/own.bin', 3)[0] == b'OWN'


def test_copy_publication_failure_cleans_owned_staging(tmp_path, monkeypatch):
    import os
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    dst = adapter.create_folder('destination')
    file_id = adapter.write_file(source, 'own.bin', b'OWN')
    before = len(os.listdir('/proc/self/fd'))
    def fail_publish(*args, **kwargs):
        assert adapter.list_children(dst) == []
        assert adapter.usage() == (6, 100)
        raise StorageUnavailable('synthetic publication failure')
    monkeypatch.setattr(pu_storage, '_rename_noreplace', fail_publish)
    with pytest.raises(StorageUnavailable):
        adapter.copy_folder_tree(source, dst, 'copy')
    assert len(os.listdir('/proc/self/fd')) == before
    assert adapter.usage() == (3, 100)
    assert adapter.list_children(dst) == []
    assert not (tmp_path / '1' / '.pu-copy-staging').exists()
    assert adapter.read_bytes(file_id, 3)[0] == b'OWN'


def test_copy_cleanup_rejects_replaced_staging_identity(tmp_path, monkeypatch):
    import os
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    staged = tmp_path / '1' / '.pu-copy-staging'
    before = len(os.listdir('/proc/self/fd'))
    replacements = []
    def replace_then_fail(src_fd, target_fd, budget):
        original = next(staged.iterdir())
        original.rename(staged / 'held-own-stage')
        original.mkdir()
        (original / 'foreign-marker').write_bytes(b'KEEP')
        replacements.append(original)
        raise OSError('synthetic stage replacement')
    monkeypatch.setattr(pu_storage, '_copy_tree_fds', replace_then_fail)
    with pytest.raises(StorageAccessDenied, match='staging identity changed'):
        adapter.copy_folder_tree(source, 'pu:/', 'copy')
    assert len(os.listdir('/proc/self/fd')) == before
    assert len(replacements) == 1
    assert (replacements[0] / 'foreign-marker').read_bytes() == b'KEEP'
    assert (staged / 'held-own-stage').is_dir()


@pytest.mark.parametrize('quota', [100, 8])
def test_completed_copy_retry_survives_new_adapter_without_extra_bytes(tmp_path, quota):
    adapter = _adapter(tmp_path, quota=quota)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    first = adapter.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='synthetic-session')
    before = adapter.usage()
    restarted = _adapter(tmp_path, quota=quota)
    second = restarted.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='synthetic-session')
    assert second == first
    assert restarted.usage() == before
    assert sorted(item.id for item in restarted.list_children('pu:/')) == sorted([source, first.copy_root_id])
    assert restarted.read_bytes(first.copy_root_id + '/own.bin', 3)[0] == b'OWN'


def test_copy_retry_never_adopts_unowned_same_name(tmp_path):
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    impostor = adapter.create_folder('copy — PU safe copy synthetic-session')
    first = adapter.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='synthetic-session')
    assert first.copy_root_id != impostor
    second = _adapter(tmp_path, quota=100).copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='synthetic-session')
    assert second == first
    assert adapter.list_children(impostor) == []


def test_copy_retry_conflicting_source_is_denied(tmp_path):
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    first_source = adapter.create_folder('first-source')
    second_source = adapter.create_folder('second-source')
    adapter.write_file(first_source, 'a.bin', b'A')
    adapter.write_file(second_source, 'b.bin', b'B')
    first = adapter.copy_folder_tree(first_source, 'pu:/', 'copy', idempotency_key='same-key')
    before = adapter.usage()
    with pytest.raises(StorageAccessDenied):
        adapter.copy_folder_tree(second_source, 'pu:/', 'copy', idempotency_key='same-key')
    assert adapter.usage() == before
    assert adapter.read_bytes(first.copy_root_id + '/a.bin', 1)[0] == b'A'


@pytest.mark.parametrize('damage', ['corrupt-receipt', 'missing-file', 'symlink-file'])
def test_copy_retry_damaged_result_never_creates_duplicate(tmp_path, damage):
    import os
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageAccessDenied
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    result = adapter.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='same-key')
    physical = tmp_path / '1' / result.copy_root_name
    if damage == 'corrupt-receipt':
        os.setxattr(physical, pu_storage.COPY_RECEIPT_XATTR, b'not-json')
    else:
        (physical / 'own.bin').unlink()
        if damage == 'symlink-file':
            (physical / 'own.bin').symlink_to(tmp_path / '1' / 'source' / 'own.bin')
    before = sorted(p.name for p in (tmp_path / '1').iterdir())
    with pytest.raises(StorageAccessDenied):
        _adapter(tmp_path, quota=100).copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='same-key')
    assert sorted(p.name for p in (tmp_path / '1').iterdir()) == before


def test_copy_ownership_metadata_unavailable_cleans_stage(tmp_path, monkeypatch):
    import os
    import errno
    from app.integrations.contracts import StorageUnavailable
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    def unsupported(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, 'synthetic xattr unsupported')
    monkeypatch.setattr(os, 'setxattr', unsupported)
    with pytest.raises(StorageUnavailable, match='ownership metadata unavailable'):
        adapter.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='same-key')
    assert [item.id for item in adapter.list_children('pu:/')] == [source]
    assert adapter.usage() == (3, 100)
    assert not (tmp_path / '1' / '.pu-copy-staging').exists()


def test_copy_receipt_metadata_size_limit_fails_before_publication(tmp_path, monkeypatch):
    from app.integrations import pu_storage
    from app.integrations.contracts import StorageQuotaExceeded
    adapter = _adapter(tmp_path, quota=100)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    monkeypatch.setattr(pu_storage, 'COPY_RECEIPT_MAX_BYTES', 1)
    with pytest.raises(StorageQuotaExceeded, match='receipt exceeds metadata limit'):
        adapter.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='same-key')
    assert adapter.usage() == (3, 100)
    assert [item.id for item in adapter.list_children('pu:/')] == [source]


def _same_key_copy_worker(root, ready, start, queue):
    try:
        adapter = PuStorageAdapter(1, root=root, quota_bytes=8)
        ready.set()
        if not start.wait(5):
            raise RuntimeError('start timeout')
        result = adapter.copy_folder_tree('pu:/source', 'pu:/', 'copy', idempotency_key='shared-session')
        queue.put(('ok', result.copy_root_id, result.id_map, result.item_count))
    except BaseException as exc:
        queue.put(('error', type(exc).__name__))


@pytest.mark.parametrize('attempt', range(2))
def test_same_key_concurrent_copy_publishes_once(tmp_path, attempt):
    import multiprocessing
    adapter = _adapter(tmp_path, quota=8)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    ctx = multiprocessing.get_context('fork')
    queue, start = ctx.Queue(), ctx.Event()
    ready = [ctx.Event(), ctx.Event()]
    workers = [ctx.Process(target=_same_key_copy_worker, args=(tmp_path, ready[i], start, queue)) for i in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert all(event.wait(5) for event in ready)
        start.set()
        for worker in workers:
            worker.join(5)
        assert all(not worker.is_alive() and worker.exitcode == 0 for worker in workers)
        outcomes = [queue.get(timeout=1) for _ in workers]
        assert all(row[0] == 'ok' for row in outcomes), outcomes
        assert outcomes[0] == outcomes[1]
        assert adapter.usage() == (6, 8)
        assert len(adapter.list_children('pu:/')) == 2
        assert not (tmp_path / '1' / '.pu-copy-staging').exists()
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(2)
        queue.close()
        queue.join_thread()


def test_compact_receipt_handles_large_path_manifest(tmp_path):
    import os
    from app.integrations import pu_storage
    adapter = _adapter(tmp_path, quota=10_000)
    source = adapter.create_folder('source')
    for index in range(80):
        (tmp_path / '1' / 'source' / (f'file-{index:03d}-' + 'x' * 48 + '.bin')).write_bytes(b'OWN')
    first = adapter.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='long-manifest')
    assert first.item_count == 80 and len(first.id_map) == 81
    metadata = os.getxattr(tmp_path / '1' / first.copy_root_name, pu_storage.COPY_RECEIPT_XATTR)
    assert len(metadata) < 1024
    replay = _adapter(tmp_path, quota=480).copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='long-manifest')
    assert replay == first
    assert adapter.usage()[0] == 480


def _crash_copy_worker(root, phase):
    import os
    from app.integrations import pu_storage
    adapter = PuStorageAdapter(1, root=root, quota_bytes=8)
    if phase == 'during-copy':
        def crash_during_copy(source_fd, target_fd, budget):
            fd = os.open('partial.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=target_fd)
            os.write(fd, b'XX')
            os.close(fd)
            os._exit(74)
        pu_storage._copy_tree_fds = crash_during_copy
    elif phase == 'after-marker':
        original = pu_storage._mark_completed_copy
        def crash_after_marker(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(75)
        pu_storage._mark_completed_copy = crash_after_marker
    else:
        original = pu_storage._rename_noreplace
        def crash_after_publish(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(73)
        pu_storage._rename_noreplace = crash_after_publish
    adapter.copy_folder_tree('pu:/source', 'pu:/', 'copy', idempotency_key='crash-session')
    os._exit(99)


@pytest.mark.parametrize('phase,expected_exit', [('after-publish', 73), ('during-copy', 74), ('after-marker', 75)])
def test_copy_process_crash_recovery_boundaries(tmp_path, monkeypatch, phase, expected_exit):
    import multiprocessing
    adapter = _adapter(tmp_path, quota=8)
    source = adapter.create_folder('source')
    adapter.write_file(source, 'own.bin', b'OWN')
    ctx = multiprocessing.get_context('fork')
    worker = ctx.Process(target=_crash_copy_worker, args=(tmp_path, phase))
    try:
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        assert worker.exitcode == expected_exit
    finally:
        if worker.is_alive():
            worker.terminate()
        if worker.pid is not None:
            worker.join(2)
    before = adapter.usage()[0]
    visible = adapter.list_children('pu:/')
    if phase == 'after-publish':
        assert len(visible) == 2 and before == 6
        completed = next(item.id for item in visible if item.id != source)
    else:
        assert [item.id for item in visible] == [source]
        assert before == (5 if phase == 'during-copy' else 6)
    if phase in ('after-publish', 'after-marker'):
        from app.integrations import pu_storage
        def forbid_recopy(*args, **kwargs):
            pytest.fail('A ready/published copy must be recovered without recopying bytes')
        monkeypatch.setattr(pu_storage, '_copy_tree_fds', forbid_recopy)
    restarted = _adapter(tmp_path, quota=8)
    result = restarted.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='crash-session')
    assert restarted.read_bytes(result.copy_root_id + '/own.bin', 3)[0] == b'OWN'
    assert len(restarted.list_children('pu:/')) == 2
    if phase == 'during-copy':
        assert restarted.usage()[0] == 8
        leftovers = list((tmp_path / '1' / '.pu-copy-staging').iterdir())
        assert len(leftovers) == 1 and (leftovers[0] / 'partial.bin').read_bytes() == b'XX'
    else:
        assert restarted.usage()[0] == before
        if phase == 'after-publish':
            assert result.copy_root_id == completed
        else:
            assert not (tmp_path / '1' / '.pu-copy-staging').exists()
    assert restarted.copy_folder_tree(source, 'pu:/', 'copy', idempotency_key='crash-session') == result
