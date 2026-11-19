"""Platform branch simulations plus real cross-process file-lock coverage."""
import ctypes
import errno
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app import platform_support as platform
from app.file_safety import ContentSafetyError, assert_no_symlinks, assert_regular_tree, safe_relative
from app.server.progress import _atomic_write, _assert_regular, _ensure_directory, StorageError


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-platform 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def lockfile(self):
        descriptor = os.open(self.root / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        return descriptor

    def windows_crt(self, side_effect=None):
        return types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=Mock(side_effect=side_effect))

    def test_windows_lock_locks_fixed_byte_without_racy_initialization_then_unlocks(self):
        descriptor = self.lockfile()
        crt = self.windows_crt()
        with patch.object(platform, 'WINDOWS', True), patch.dict(sys.modules, {'msvcrt': crt}):
            with platform.file_lock(descriptor, exclusive=False):
                self.assertEqual(os.fstat(descriptor).st_size, 0)
                os.lseek(descriptor, 20, os.SEEK_SET)
            self.assertEqual(os.lseek(descriptor, 0, os.SEEK_CUR), 0)
        self.assertEqual(crt.locking.call_args_list[0].args, (descriptor, 2, 1))
        self.assertEqual(crt.locking.call_args_list[1].args, (descriptor, 0, 1))

    def test_windows_lock_retries_contention_and_releases_on_exception(self):
        descriptor = self.lockfile()
        crt = self.windows_crt([PermissionError(errno.EACCES, 'busy'), None, None])
        with patch.object(platform, 'WINDOWS', True), patch.dict(sys.modules, {'msvcrt': crt}), patch.object(platform.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'callback'), platform.file_lock(descriptor):
                raise ValueError('callback')
        self.assertEqual(crt.locking.call_count, 3)
        sleep.assert_called_once_with(.05)

    def test_windows_nonblocking_lock_reports_busy_without_unlocking_other_owner(self):
        descriptor = self.lockfile()
        crt = self.windows_crt(PermissionError(errno.EACCES, 'busy'))
        with patch.object(platform, 'WINDOWS', True), patch.dict(sys.modules, {'msvcrt': crt}), patch.object(platform.time, 'sleep') as sleep:
            with self.assertRaises(BlockingIOError), platform.file_lock(descriptor, blocking=False):
                self.fail('must not acquire')
        self.assertEqual(crt.locking.call_count, 1)
        sleep.assert_not_called()

    def test_windows_lock_does_not_swallow_unrelated_io_error(self):
        descriptor = self.lockfile()
        crt = self.windows_crt(OSError(errno.EIO, 'disk'))
        with patch.object(platform, 'WINDOWS', True), patch.dict(sys.modules, {'msvcrt': crt}), self.assertRaises(OSError):
            with platform.file_lock(descriptor):
                self.fail('must not acquire')
        self.assertEqual(crt.locking.call_count, 1)

    def test_file_lock_is_cross_process_and_released(self):
        descriptor = self.lockfile()
        code = ('import os,sys; from app.platform_support import file_lock; '
                'fd=os.open(sys.argv[1],os.O_RDWR); '
                'lock=file_lock(fd,blocking=False); lock.__enter__(); lock.__exit__(None,None,None)')
        with platform.file_lock(descriptor):
            result = subprocess.run([sys.executable, '-B', '-c', code, str(self.root / 'lock')], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'BlockingIOError', result.stderr)
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.root / 'lock')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_windows_replace_retries_then_succeeds(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_bytes(b'new'); target.write_bytes(b'old')
        actual = os.replace
        calls = []
        def busy_then_write(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(errno.EACCES, 'sharing violation')
            actual(src, dst)
        with patch.object(platform, 'WINDOWS', True), patch.object(platform.os, 'replace', busy_then_write), patch.object(platform.time, 'sleep') as sleep:
            platform.atomic_replace(source, target)
        self.assertEqual(target.read_bytes(), b'new')
        self.assertEqual(sleep.call_count, 2)

    def test_windows_replace_exhaustion_preserves_old_record_and_reports_error(self):
        target = self.root / 'record.json'; target.write_bytes(b'old')
        with patch.object(platform, 'WINDOWS', True), patch.object(platform.os, 'replace', side_effect=PermissionError('busy')) as replace, patch.object(platform.time, 'sleep'):
            with self.assertRaises(PermissionError):
                _atomic_write(target, b'new')
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(target.read_bytes(), b'old')
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_windows_replace_does_not_retry_disk_errors(self):
        with patch.object(platform, 'WINDOWS', True), patch.object(platform.os, 'replace', side_effect=OSError(errno.ENOSPC, 'disk full')) as replace, patch.object(platform.time, 'sleep') as sleep:
            with self.assertRaises(OSError): platform.atomic_replace('source', 'target')
        self.assertEqual(replace.call_count, 1); sleep.assert_not_called()

    def test_posix_replace_does_not_retry_permission_error(self):
        with patch.object(platform, 'WINDOWS', False), patch.object(platform.os, 'replace', side_effect=PermissionError('denied')) as replace:
            with self.assertRaises(PermissionError): platform.atomic_replace('source', 'target')
        self.assertEqual(replace.call_count, 1)

    def test_directory_sync_is_skipped_on_windows_and_errors_on_posix(self):
        with patch.object(platform, 'WINDOWS', True), patch.object(platform.os, 'open') as opened:
            platform.sync_directory(self.root)
        opened.assert_not_called()
        with patch.object(platform, 'WINDOWS', False), patch.object(platform.os, 'fsync', side_effect=OSError('sync failed')):
            with self.assertRaises(OSError): platform.sync_directory(self.root)

    def test_downloads_known_folder_handles_relocated_unicode_path(self):
        buffer = ctypes.create_unicode_buffer('D:\\迁移目录\\下载')
        def known(guid, flags, token, pointer):
            self.assertEqual(guid._obj.Data1, 0x374DE290)
            pointer._obj.value = ctypes.addressof(buffer)
            return 0
        shell = types.SimpleNamespace(SHGetKnownFolderPath=Mock(side_effect=known))
        ole = types.SimpleNamespace(CoTaskMemFree=Mock())
        with patch.object(platform, 'WINDOWS', True), patch.object(ctypes, 'WinDLL', side_effect=[shell, ole], create=True):
            self.assertEqual(str(platform.downloads_directory()), 'D:\\迁移目录\\下载')
        ole.CoTaskMemFree.assert_called_once()

    def test_downloads_failure_does_not_silently_inspect_wrong_folder(self):
        shell = types.SimpleNamespace(SHGetKnownFolderPath=Mock(return_value=-1))
        ole = types.SimpleNamespace(CoTaskMemFree=Mock())
        with patch.object(platform, 'WINDOWS', True), patch.object(ctypes, 'WinDLL', side_effect=[shell, ole], create=True):
            with self.assertRaisesRegex(OSError, '下载'): platform.downloads_directory()
        ole.CoTaskMemFree.assert_not_called()

    def test_process_environment_keeps_os_paths_but_not_python_injection(self):
        with patch.object(platform, 'WINDOWS', True), patch.dict(os.environ, {'SystemRoot': 'C:\\Windows', 'TEMP': 'C:\\Temp', 'PYTHONPATH': 'malicious'}):
            env = platform.subprocess_environment()
            self.assertEqual(env['SystemRoot'], 'C:\\Windows')
            self.assertEqual(env['TEMP'], 'C:\\Temp')
            self.assertNotIn('PYTHONPATH', env)
            self.assertEqual(platform.hidden_process_kwargs()['creationflags'], 0x08000000)
        with patch.object(platform, 'WINDOWS', False):
            self.assertEqual(platform.hidden_process_kwargs(), {})

    def test_reveal_uses_argument_array_with_no_shell(self):
        path = self.root / '有空格 文件.txt'
        for windows in (False, True):
            with self.subTest(windows=windows), patch.object(platform, 'WINDOWS', windows), patch.object(platform.subprocess, 'Popen') as call:
                platform.reveal_file(path)
                args, kwargs = call.call_args
                self.assertEqual(args[0], ['explorer.exe', '/select,', str(path)] if windows else ['open', '-R', str(path)])
                self.assertNotIn('shell', kwargs)

    def test_windows_ambiguous_paths_rejected_on_every_platform(self):
        for path in ('data/CON.txt', 'data/nul', 'docs/AUX.csv', 'data/com1.txt', 'docs/LPT9',
                     'docs/COM¹', 'docs/COM1 .txt', 'docs/name.', 'docs/name ', 'docs/a|b', 'docs/a?b', 'docs/a*b'):
            with self.subTest(path=path), self.assertRaises(ContentSafetyError): safe_relative(path)
        self.assertEqual(str(safe_relative('docs/normal-中文.txt')), 'docs/normal-中文.txt')

    def test_reparse_directory_is_refused_before_scanning(self):
        actual = Path.lstat
        root = self.root
        def junction(path, *args, **kwargs):
            if path == root / 'data':
                return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)
            return actual(path, *args, **kwargs)
        with patch.object(Path, 'lstat', junction), patch('app.file_safety.os.scandir') as scan:
            with self.assertRaises(ContentSafetyError): assert_no_symlinks(root, 'data/file.txt')
            with self.assertRaises(ContentSafetyError): assert_regular_tree(root, 'data')
        scan.assert_not_called()

    def test_record_reparse_file_or_directory_is_refused(self):
        directory = self.root / 'progress'
        target = directory / 'settings.json'
        actual = Path.lstat
        def linked(path, *args, **kwargs):
            if path in (directory, target):
                return types.SimpleNamespace(st_mode=stat.S_IFDIR if path == directory else stat.S_IFREG, st_file_attributes=0x400)
            return actual(path, *args, **kwargs)
        with patch.object(Path, 'lstat', linked):
            with self.assertRaises(StorageError): _assert_regular(target)
            with self.assertRaises(StorageError): _ensure_directory(directory)
