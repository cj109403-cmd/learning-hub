"""Runtime-only, atomic JSON storage with a shared process lock and raw backups.

Development and tests must inject a directory in a temporary project. This module
never discovers a project root and never inspects workspace or course content.
"""

import hashlib
import json
import os
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.platform_support import atomic_replace, file_lock, sync_directory
from app.file_safety import is_reparse_point

from .migrations import (CURRENT_VERSION, MigrationError, UnsupportedVersion,
                         migrate, new_progress, validate_progress)


DEFAULT_SETTINGS = {"theme": "clear", "mode": "auto", "reading": "sans",
                    "size": "m", "motion": "system", "sidebar_collapsed": False,
                    "card_tips_seen": False, "require_summary": False}
SETTING_FLAGS = ("sidebar_collapsed", "card_tips_seen", "require_summary")
SETTING_CHOICES = {"theme": {"paper", "clear"}, "mode": {"auto", "light", "dark"},
                   "reading": {"sans", "serif"}, "size": {"m", "l"},
                   "motion": {"system", "reduce"}}


class StorageError(RuntimeError):
    status = 503


class FieldConflict(Exception):
    """Typed text was changed elsewhere since this edit started (the field's base differs)."""


class ConflictError(StorageError):
    status = 409

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.revision = snapshot["revision"]
        super().__init__("记录已在别处更新。请重新加载记录；尚未保存的草稿会保留。")


def local_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stamp():
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f%z") + "-" + uuid.uuid4().hex[:8]


def _revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 中出现重复字段。")
        result[key] = value
    return result


def _decode(raw):
    def invalid_constant(value):
        raise ValueError("JSON 中出现无效数字。")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                      parse_constant=invalid_constant)


def _fsync_directory(directory):
    sync_directory(directory)

def _assert_regular(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if is_reparse_point(info) or not stat.S_ISREG(info.st_mode):
        raise StorageError("记录文件不是普通文件，已停止写入：%s" % path.name)


def _ensure_directory(directory):
    # Check only this explicitly supplied ancestor chain, never enumerate siblings.
    for part in reversed((directory,) + tuple(directory.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            part.mkdir(mode=0o700)
        else:
            if is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
                raise StorageError("记录目录包含非普通目录或符号链接，已停止写入。")


def _atomic_write(path, raw):
    """A failure never exposes partial JSON; failures after replace remain errors."""
    _assert_regular(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if handle.write(raw) != len(raw):
                raise OSError("临时记录没有完整写入。")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary, str(path))
        _fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class ProgressStore:
    def __init__(self, progress_dir):
        supplied = Path(progress_dir)
        if not supplied.is_absolute():
            raise ValueError("ProgressStore 必须显式传入绝对路径。")
        # macOS exposes /var and /tmp through system symlinks. Canonicalize the
        # explicitly supplied parent, but keep the storage leaf unfollowed so a
        # progress-directory symlink itself is still refused.
        self.directory = supplied.parent.resolve() / supplied.name
        self.path = self.directory / "progress.json"
        self.settings_path = self.directory / "settings.json"
        self.backups = self.directory / "backups"
        self._thread_lock = threading.RLock()
        self._started = False
        self._read_only = False
        self._notices = []
        self._last_progress = new_progress()
        self._last_raw = b""

    @contextmanager
    def _lock(self):
        with self._thread_lock:
            _ensure_directory(self.directory)
            lock_path = self.directory / ".write.lock"
            _assert_regular(lock_path)
            descriptor = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                with file_lock(descriptor):
                    yield
            finally:
                os.close(descriptor)

    def _notice(self, message):
        if message not in self._notices:
            self._notices.append(message)

    def _snapshot(self, progress=None, raw=None):
        if progress is not None:
            self._last_progress = deepcopy(progress)
        if raw is not None:
            self._last_raw = raw
        return {"progress": deepcopy(self._last_progress), "revision": _revision(self._last_raw),
                "notices": list(self._notices), "read_only": self._read_only}

    def _read_file(self, path):
        _assert_regular(path)
        raw = path.read_bytes()
        progress = _decode(raw)
        validate_progress(progress)
        return progress, raw

    def _backup_files(self):
        if not self.backups.exists():
            return []
        _ensure_directory(self.backups)
        files = list(self.backups.glob("progress-*.json"))
        for path in files:
            _assert_regular(path)
        return sorted(files, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)

    def _backup(self, raw):
        _ensure_directory(self.backups)
        backup = self.backups / ("progress-" + _stamp() + ".json")
        _atomic_write(backup, raw)
        # Never prune before the new copy is safely written.
        for old in self._backup_files()[20:]:
            old.unlink()
        _fsync_directory(self.backups)
        return backup

    def _recover(self, raw=None):
        if raw is not None:
            quarantine = self.directory / ("progress.corrupt-" + _stamp() + ".json")
            atomic_replace(str(self.path), str(quarantine))
            _fsync_directory(self.directory)
            self._notice("学习记录文件损坏，原件已保留为 %s。" % quarantine.name)
        for backup in self._backup_files():
            try:
                progress, backup_raw = self._read_file(backup)
            except UnsupportedVersion as exc:
                self._read_only = True
                self._notice(str(exc))
                return None
            except (ValueError, UnicodeError):
                continue
            # Back up before a restored old format is migrated, too.
            self._backup(backup_raw)
            migrated = migrate(progress)
            restored_raw = backup_raw if migrated == progress else _json_bytes(migrated)
            _atomic_write(self.path, restored_raw)
            self._notice("已从备份 %s 恢复学习记录；备份之后的操作可能需要重新补回。" % backup.name)
            return migrated, restored_raw
        self._read_only = True
        self._notice("没有找到可用的学习记录备份，已停止写入。损坏原件仍保留，请先请 Codex 协助恢复；程序不会建立空记录覆盖它。")
        return None

    def startup(self):
        """Back up first; quarantine only corruption, never an unsupported version."""
        with self._lock():
            self._read_only = False
            self._notices = []
            self._started = True
            try:
                _assert_regular(self.path)
                if not self.path.exists():
                    if self._backup_files() or any(self.directory.glob("progress.corrupt-*.json")):
                        recovered = self._recover()
                        return self._snapshot(*recovered) if recovered else self._snapshot()
                    progress = new_progress()
                    raw = _json_bytes(progress)
                    _atomic_write(self.path, raw)
                    # A fresh startup is backed up just like every later startup.
                    self._backup(raw)
                    return self._snapshot(progress, raw)
                raw = self.path.read_bytes()
                try:
                    progress = _decode(raw)
                    validate_progress(progress)
                except UnsupportedVersion as exc:
                    self._read_only = True
                    self._notice(str(exc))
                    return self._snapshot(progress, raw)
                except (ValueError, UnicodeError):
                    recovered = self._recover(raw)
                    return self._snapshot(*recovered) if recovered else self._snapshot()
                self._snapshot(progress, raw)
                self._backup(raw)
                migrated = migrate(progress)
                if migrated != progress:
                    raw = _json_bytes(migrated)
                    _atomic_write(self.path, raw)
                    self._notice("学习记录已安全升级到版本 %s，升级前原件已备份。" % CURRENT_VERSION)
                return self._snapshot(migrated, raw)
            except (OSError, ValueError, StorageError) as exc:
                self._read_only = True
                self._notice("学习记录暂时不能安全写入：%s。请保留记录目录，排查后重新启动。" % exc)
                # A directory fsync may fail after replacement; show actual complete bytes.
                try:
                    progress, raw = self._read_file(self.path)
                    return self._snapshot(progress, raw)
                except (OSError, ValueError, StorageError):
                    return self._snapshot()

    def _read_locked(self):
        try:
            progress, raw = self._read_file(self.path)
            if progress["spec_version"] != CURRENT_VERSION:
                raise StorageError("学习记录格式发生变化，请重新启动以先备份再升级。")
            return self._snapshot(progress, raw)
        except UnsupportedVersion as exc:
            self._read_only = True
            self._notice(str(exc))
        except (OSError, ValueError, StorageError) as exc:
            self._read_only = True
            self._notice("学习记录目前无法安全读取，已暂停写入：%s。请重新启动以检查备份。" % exc)
        return self._snapshot()

    def read(self):
        if not self._started:
            return self.startup()
        with self._lock():
            return self._read_locked()

    def mutate(self, revision, callback, *, check_revision=True):
        """Apply callback to a copy of the record and write it atomically.

        check_revision=False is for operations that cannot overwrite someone else's
        work (program state, set-style marks) or that check their own field instead:
        the callback raises FieldConflict when that field changed in the meantime.
        """
        if not self._started:
            self.startup()
        with self._lock():
            snapshot = self._read_locked()
            if self._read_only:
                raise StorageError(snapshot["notices"][-1] if snapshot["notices"] else "学习记录已暂停写入。")
            if check_revision and revision != snapshot["revision"]:
                raise ConflictError(snapshot)
            progress = deepcopy(snapshot["progress"])
            try:
                result = callback(progress)
            except FieldConflict:
                raise ConflictError(snapshot) from None
            try:
                validate_progress(progress)
                if progress["spec_version"] != CURRENT_VERSION:
                    raise MigrationError("保存不能改变学习记录的格式版本。")
                raw = _json_bytes(progress)
                _atomic_write(self.path, raw)
            except (OSError, ValueError, StorageError) as exc:
                raise StorageError("学习记录没有确认保存成功，输入草稿仍应保留：%s" % exc) from exc
            return self._snapshot(progress, raw), result

    def _settings_locked(self):
        _assert_regular(self.settings_path)
        if not self.settings_path.exists():
            return deepcopy(DEFAULT_SETTINGS)
        settings = _decode(self.settings_path.read_bytes())
        if not isinstance(settings, dict):
            raise ValueError("外观设置不是对象。")
        result = deepcopy(DEFAULT_SETTINGS)
        result.update(settings)
        self._validate_settings(result)
        return result

    @staticmethod
    def _validate_settings(settings):
        for field, choices in SETTING_CHOICES.items():
            if not isinstance(settings.get(field), str) or settings[field] not in choices:
                raise ValueError("外观设置 %s 的取值不正确。" % field)
        for field in SETTING_FLAGS:
            if type(settings.get(field)) is not bool:
                raise ValueError("外观设置 %s 的取值不正确。" % field)

    def get_settings(self):
        with self._lock():
            try:
                return self._settings_locked()
            except (OSError, ValueError, StorageError) as exc:
                self._notice("外观设置无法读取，暂用清透默认外观；原文件没有修改：%s" % exc)
                return deepcopy(DEFAULT_SETTINGS)

    def put_settings(self, changes):
        if not isinstance(changes, dict) or set(changes) - set(DEFAULT_SETTINGS):
            raise ValueError("外观设置包含未识别的选项。")
        with self._lock():
            try:
                settings = self._settings_locked()
                settings.update(changes)
                self._validate_settings(settings)
                _atomic_write(self.settings_path, _json_bytes(settings))
                return settings
            except OSError as exc:
                raise StorageError("外观设置没有确认保存成功：%s" % exc) from exc
