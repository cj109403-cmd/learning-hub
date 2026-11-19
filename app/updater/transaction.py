"""Crash-recoverable replacement of exactly seven content roots."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from app.platform_support import atomic_replace, file_lock, sync_directory

from app.file_safety import (CONTENT_ROOTS, ContentSafetyError, absolute_root,
                             assert_no_symlinks, assert_regular_tree)

MANIFEST = ".content-update-transaction.json"
LOCK = ".content-update.lock"
BACKUP_MARKER = ".learning-hub-backup.json"
BACKUP_RE = re.compile(r"content-\d{8}-\d{6}-\d{6}-[a-f0-9]{8}$")
STAGE_RE = re.compile(r"\.content-stage-[a-zA-Z0-9_-]+$")


class PendingUpdateError(ContentSafetyError):
    restored = False


def atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent),
                                         prefix=".transaction-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def backup_directory(root):
    root = absolute_root(root)
    backups = assert_no_symlinks(root, "backups")
    backups.mkdir(exist_ok=True)
    return backups


@contextmanager
def content_lock(root, exclusive=False):
    """Shared content readers fail closed while an interrupted update awaits recovery."""
    root = absolute_root(root)
    backups = backup_directory(root)
    path = assert_no_symlinks(root, "backups/" + LOCK)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with file_lock(descriptor, exclusive=exclusive):
            manifest = assert_no_symlinks(root, "backups/" + MANIFEST)
            if not exclusive and manifest.exists():
                raise PendingUpdateError("上次内容更新未完成。请重新启动学习台或运行备用更新工具恢复。")
            yield
    finally:
        os.close(descriptor)


def _remove(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _sync_file(path):
    # Windows FlushFileBuffers (used by fsync) requires a writable handle.
    # These files are our own copies; r+b preserves their bytes without truncation.
    mode = "r+b" if os.name == "nt" else "rb"
    with open(path, mode) as handle:
        os.fsync(handle.fileno())


def _sync_tree(root):
    """Flush only a known staging/backup tree, before publishing its manifest."""
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        for filename in filenames:
            _sync_file(Path(directory) / filename)
        sync_directory(directory)


def _read_manifest(root):
    path = assert_no_symlinks(root, "backups/" + MANIFEST)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise PendingUpdateError("更新事务清单损坏，已停止。请保留 backups 并请 Codex 检查。") from exc
    if (not isinstance(data, dict) or data.get("version") != 1 or
            not BACKUP_RE.fullmatch(str(data.get("backup", ""))) or
            not STAGE_RE.fullmatch(str(data.get("stage", ""))) or
            not isinstance(data.get("existed"), dict) or
            set(data.get("existed", {})) != set(CONTENT_ROOTS) or
            any(type(value) is not bool for value in data.get("existed", {}).values()) or
            data.get("state") not in ("prepared", "replacing", "validating", "committed", "rolling_back")):
        raise PendingUpdateError("更新事务清单不符合格式，已停止。请保留 backups 并请 Codex 检查。")
    return data


def _cleanup_stage(root, manifest):
    relative = "backups/" + manifest["stage"]
    assert_regular_tree(root, relative)
    _remove(root / relative)


def rollback_locked(root, manifest):
    """The caller holds the exclusive lock. Repeating rollback is safe."""
    root = absolute_root(root)
    backups = root / "backups"
    backup = backups / manifest["backup"]
    assert_regular_tree(root, "backups/" + manifest["backup"])
    # Before deleting any current root, ensure every required backup still exists.
    for allowed in CONTENT_ROOTS:
        saved = assert_no_symlinks(backup, allowed)
        if manifest["existed"][allowed] and not saved.exists():
            raise PendingUpdateError("内容备份不完整，已停止恢复。请保留 backups 并请 Codex 检查。")
    manifest["state"] = "rolling_back"
    atomic_json(backups / MANIFEST, manifest)
    for allowed in CONTENT_ROOTS:
        assert_regular_tree(root, allowed)
        target = root / allowed
        _remove(target)
        if manifest["existed"][allowed]:
            _copy(backup / allowed, target)
            if target.is_dir():
                _sync_tree(target)
            else:
                _sync_file(target)
                sync_directory(target.parent)
    _cleanup_stage(root, manifest)
    (backups / MANIFEST).unlink()
    sync_directory(backups)


def recover_locked(root):
    root = absolute_root(root)
    manifest = _read_manifest(root)
    if not manifest:
        return False
    if manifest["state"] == "committed":
        _cleanup_stage(root, manifest)
        (root / "backups" / MANIFEST).unlink()
        sync_directory(root / "backups")
    else:
        rollback_locked(root, manifest)
    return True


def recover_pending(root):
    with content_lock(root, exclusive=True):
        return recover_locked(root)


def trim_backups(root, keep=10):
    backups = root / "backups"
    candidates = []
    # Only our stamped, complete backups are owned by this tool. Manual/legacy
    # backup folders remain untouched; no arbitrary backups subtree is scanned.
    with os.scandir(backups) as entries:
        names = [entry.name for entry in entries if BACKUP_RE.fullmatch(entry.name)]
    for name in names:
        base = assert_no_symlinks(backups, name)
        marker = assert_no_symlinks(base, BACKUP_MARKER)
        if marker.is_file():
            try:
                metadata = json.loads(marker.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if (isinstance(metadata, dict) and metadata.get("owner") == "learning-hub-updater-v1"
                    and metadata.get("complete") is True):
                candidates.append(base)
    for old in sorted(candidates, key=lambda path: path.name, reverse=True)[keep:]:
        assert_regular_tree(backups, old.name)
        shutil.rmtree(old)


def replace_locked(root, package, validate, on_step=None):
    """Back up first, journal before mutation, and preserve backup on all errors."""
    root = absolute_root(root)
    backups = root / "backups"
    on_step = on_step or (lambda step: None)
    for allowed in CONTENT_ROOTS:
        assert_regular_tree(root, allowed)
    stage = Path(tempfile.mkdtemp(prefix=".content-stage-", dir=str(backups)))
    backup = backups / ("content-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:8])
    manifest = {"version": 1, "backup": backup.name, "stage": stage.name,
                "state": "prepared", "existed": {}}
    journal_published = False
    committed = False
    try:
        package.extract(stage)
        # A root the package leaves out stays as it is: stage the current copy so the
        # journalled replace-and-rollback below treats every root the same way.
        for allowed in CONTENT_ROOTS:
            if allowed not in getattr(package, "roots", CONTENT_ROOTS) and (root / allowed).exists():
                (stage / allowed).parent.mkdir(parents=True, exist_ok=True)
                _copy(root / allowed, stage / allowed)
        backup.mkdir()
        for allowed in CONTENT_ROOTS:
            source = root / allowed
            manifest["existed"][allowed] = source.exists()
            if source.exists():
                _copy(source, backup / allowed)
        _sync_tree(backup)
        atomic_json(backup / BACKUP_MARKER, {"owner": "learning-hub-updater-v1", "complete": True})
        _sync_tree(stage)
        atomic_json(backups / MANIFEST, manifest)
        journal_published = True
        on_step("prepared")
        for allowed in CONTENT_ROOTS:
            manifest["state"] = "replacing"
            manifest["next_root"] = allowed
            atomic_json(backups / MANIFEST, manifest)
            on_step("before_replace:" + allowed)
            target = assert_no_symlinks(root, allowed)
            if not os.path.lexists(stage / allowed) and not os.path.lexists(target):
                on_step("after_replace:" + allowed)  # neither the package nor the project has it
                continue
            _remove(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_replace(stage / allowed, target)
            sync_directory(target.parent)
            on_step("after_replace:" + allowed)
        manifest["state"] = "validating"
        atomic_json(backups / MANIFEST, manifest)
        on_step("before_validate")
        report = validate(root)
        if report["code"] != 0:
            raise ContentSafetyError("内容校验失败，已恢复原版本。\n" + report["output"])
        on_step("after_validate")
        manifest["state"] = "committed"
        atomic_json(backups / MANIFEST, manifest)
        committed = True
        on_step("committed")
        _cleanup_stage(root, manifest)
        (backups / MANIFEST).unlink()
        sync_directory(backups)
        trim_backups(root)
        return {"backup": str(backup), "validation_output": report["output"]}
    except BaseException as failure:
        if journal_published and not committed:
            try:
                rollback_locked(root, manifest)
            except BaseException as rollback_error:
                raise PendingUpdateError("更新中断，自动恢复未完成。原内容备份和事务清单已保留，请再次运行更新器或请 Codex 检查。") from rollback_error
        elif not journal_published:
            _remove(stage)
            # An incomplete backup is retained for inspection, never rotated.
        failure.restored = not committed
        raise
