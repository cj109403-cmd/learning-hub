"""Small standard-library OS boundary shared by the service and desktop shells.

Windows uses a conservative one-byte exclusive lock, including for readers;
this serializes readers but preserves the same consistency as POSIX flock.
"""
from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

WINDOWS = sys.platform == "win32"


@contextmanager
def file_lock(descriptor, exclusive=True, blocking=True):
    if not WINDOWS:
        import fcntl
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            mode |= fcntl.LOCK_NB
        fcntl.flock(descriptor, mode)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    import msvcrt
    # CRT explicitly permits locking past EOF. Leave a new file empty: writing
    # a sentinel before acquiring the lock races with another process locking it.
    # All handles lock the same byte; LK_LOCK's implicit 10 retries are avoided.
    while True:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            break
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and getattr(exc, "winerror", None) not in (32, 33):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, "文件正被另一个学习台使用") from exc
            time.sleep(0.05)
    try:
        yield
    finally:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)


def atomic_replace(source, destination, *, attempts=5):
    """Retry Windows sharing violations only; exhaustion is always an error."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            retryable = (getattr(exc, "winerror", None) in (5, 32, 33) or
                         isinstance(exc, PermissionError))
            if not WINDOWS or not retryable or attempt + 1 == attempts:
                raise
            time.sleep(0.025 * (attempt + 1))


def sync_directory(path):
    # Windows does not expose directory fsync through Python's standard library.
    # File contents are flushed before replacement; do not mask POSIX failures.
    if WINDOWS:
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _windows_downloads():
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]
    folder_id = GUID.from_buffer_copy(uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    get_folder = shell.SHGetKnownFolderPath
    get_folder.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p)]
    get_folder.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.c_void_p()
    code = get_folder(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer))
    if code != 0:
        raise OSError("无法找到 Windows 下载文件夹（错误 {}）".format(code))
    try:
        return Path(ctypes.wstring_at(pointer))
    finally:
        ole.CoTaskMemFree(pointer)


def process_alive(pid, *, parent=False):
    """Whether a process still exists. POSIX children check their own parent link,
    which also covers a reused pid after the parent has gone."""
    if os.name != "nt":
        if parent:
            return os.getppid() == pid
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_uint32()
        ok = kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def downloads_directory():
    """Honor relocated Windows Downloads through its Known Folder identifier."""
    return _windows_downloads() if WINDOWS else Path.home() / "Downloads"


def hidden_process_kwargs():
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if WINDOWS else {}


def subprocess_environment():
    """Isolated Python workers still need Windows' OS and temporary directories."""
    result = {"PATH": os.defpath, "LANG": "zh_CN.UTF-8", "PYTHONIOENCODING": "utf-8",
              "PYTHONDONTWRITEBYTECODE": "1"}
    if WINDOWS:
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA"):
            if key in os.environ:
                result[key] = os.environ[key]
    return result


def reveal_file(path):
    path = Path(path).absolute()
    if WINDOWS:
        # Keep the switch outside the quoted path. Combining them makes
        # list2cmdline quote "/select,C:\\path with spaces" as one argument,
        # which Explorer misinterprets and opens the default folder instead.
        return subprocess.Popen(["explorer.exe", "/select,", str(path)], **hidden_process_kwargs())
    return subprocess.Popen(["open", "-R", str(path)])
