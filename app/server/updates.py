"""Application update jobs. Never open progress or workspace, even for inspection."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading

from app.platform_support import downloads_directory, hidden_process_kwargs, subprocess_environment
from app.updater.update_content import inspect_update
from app.file_safety import ContentSafetyError, assert_no_symlinks
from .lifecycle import LifecycleError


def public_error(error):
    """Only our own bounded messages may reach a learning page.

    The built-in content check (app/updater/content_check.py) reports card ids and
    fixed problem descriptions only, so its first lines can be shown; anything else
    is summarised instead of echoed.
    """
    message = str(error)
    if message.startswith(('内容校验失败', '内容校验未通过')):
        lines = [line for line in message.splitlines()[1:] if line.strip()][:4]
        return '课程内容检查未通过，现有内容保持不动。' + ('\n' + '\n'.join(lines) if lines else '')
    first = message.splitlines()[0] if message else ''
    safe_prefixes = (
        '更新包', '课程版本在检查后', '课程或更新包已改变', '课程版本格式不正确',
        '这份更新使用当前程序不支持', '不能使用符号链接', '不能使用特殊文件',
        '文件路径', '内容目录包含链接或特殊文件', '此路径必须',
        '文件和目录路径冲突', '文件类型和路径不一致', '不支持加密更新包',
        '上次内容更新未完成', '更新事务清单', '内容备份不完整',
        '更新中断，自动恢复未完成', '更新被中止', '更新进程异常结束',
    )
    if len(first) <= 500 and first.startswith(safe_prefixes):
        return first
    if isinstance(error, subprocess.TimeoutExpired):
        return '课程校验超时，请把更新包交给 Codex 检查。'
    if isinstance(error, OSError):
        return '无法访问更新包或写入课程文件，请检查文件是否仍在、项目是否可写，再重试。'
    return '这次更新检查或应用未完成，请保留原更新包并请 Codex 检查。'


def version_key(value):
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})\.(\d+)', str(value))
    if not match:
        raise ContentSafetyError('课程版本格式不正确。')
    return date.fromisoformat(match[1]), int(match[2])


class UpdateManager:
    def __init__(self, root, lifecycle, *, downloads=None):
        self.root = Path(root)
        self.lifecycle = lifecycle
        self.downloads = downloads
        self.guard = threading.RLock()
        self.state = {'state': 'idle', 'message': '', 'busy': False}
        self.candidate = None
        self.thread = None
        self.process = None

    def status(self):
        with self.guard:
            return deepcopy(self.state)

    def _set(self, state, message, **extra):
        with self.guard:
            self.state = dict(state=state, message=message,
                              busy=state in ('checking', 'updating'), **extra)

    def check(self):
        with self.guard:
            if self.state['busy']:
                raise LifecycleError('已有检查或更新正在进行。', 409)
            operation = self.lifecycle.operation()
            operation.__enter__()
            self.candidate = None
            self._set('checking', '正在检查下载文件夹中的课程更新。')
            self.thread = threading.Thread(target=self._check, args=(operation,), name='content-check')
            try:
                self.thread.start()
            except Exception:
                operation.__exit__(None, None, None)
                self._set('error', '无法启动更新检查。')
                raise
            return self.status()

    def _check(self, operation):
        try:
            folder = Path(self.downloads) if self.downloads is not None else downloads_directory()
            folder = assert_no_symlinks(folder)
            paths = []
            if folder.is_dir():
                # Only this known directory's top level; never enumerate the project.
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if (entry.name.startswith('learning-hub-update-') and entry.name.endswith('.zip')
                                and entry.is_file(follow_symlinks=False)):
                            paths.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name, Path(entry.path)))
            candidates, problems = [], []
            for unused, name, path in sorted(paths, reverse=True):
                try:
                    if path.stat().st_size > 64 * 1024 * 1024:
                        raise ContentSafetyError('更新包超过 64 MiB，请先请 Codex 检查。')
                    report = inspect_update(self.root, path)
                    if version_key(report['new_version']) <= version_key(report['old_version']):
                        continue
                    fingerprint = report['fingerprint']
                    public = dict(id=fingerprint, filename=name, old_version=report['old_version'],
                                  new_version=report['new_version'],
                                  new_cards=[{'code': c['code'], 'title': c['title']} for c in report['new_cards']])
                    candidates.append((version_key(report['new_version']), public, path))
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    problems.append(name + '：' + public_error(exc))
            if candidates:
                unused, public, path = max(candidates, key=lambda item: item[0])
                with self.guard:
                    self.candidate = (public, path)
                    self._set('available', '找到可用的课程更新，请确认后应用。', candidate=public, issues=problems)
            elif problems:
                self._set('error', '没有可安全应用的更新包。', issues=problems)
            else:
                self._set('none', '下载文件夹里没有比当前版本新的课程更新。')
        except Exception as exc:
            self._set('error', '检查没有完成：' + public_error(exc))
        finally:
            operation.__exit__(None, None, None)

    def apply(self, candidate_id):
        with self.guard:
            if self.state['busy'] or not self.candidate or candidate_id != self.candidate[0]['id']:
                raise LifecycleError('更新信息已改变，请重新检查。', 409)
            self.lifecycle.reserve_update()
            public, path = self.candidate
            self._set('updating', '正在等待已接收的保存完成。', candidate=public, step='draining')
            self.thread = threading.Thread(target=self._apply, args=(deepcopy(public), path), name='content-update')
            try:
                self.thread.start()
            except Exception:
                self.lifecycle.finish_update()
                self._set('error', '无法启动更新。现有内容保持原样。')
                raise
            return self.status()

    def _apply(self, public, path):
        final = ('error', '更新没有完成，现有内容保持原样。', {})
        try:
            self.lifecycle.wait_drained()
            if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
                raise ContentSafetyError('更新包已改变，请重新检查。')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != public['id']:
                raise ContentSafetyError('更新包已改变，请重新检查。')
            # Child reads an immutable copy owned by this job, not a mutable Downloads path.
            with tempfile.TemporaryDirectory(prefix='learning-hub-update-job-') as directory:
                archive = Path(directory) / path.name
                archive.write_bytes(raw)
                report = inspect_update(self.root, archive)
                if report['old_version'] != public['old_version'] or report['new_version'] != public['new_version']:
                    raise ContentSafetyError('课程或更新包已改变，请重新检查。')
                command = [sys.executable, '-B', '-m', 'app.updater.update_content', str(archive),
                           '--root', str(self.root), '--non-interactive', '--expected-sha256', public['id'],
                           '--expected-content-version', public['old_version']]
                code_root = Path(__file__).resolve().parents[2]
                with tempfile.TemporaryFile() as errors:
                    child = subprocess.Popen(command, cwd=str(code_root), env=subprocess_environment(),
                                             stdout=subprocess.PIPE, stderr=errors,
                                             **hidden_process_kwargs())
                    with self.guard:
                        self.process = child
                    result, failure = None, None
                    for line in child.stdout:
                        message = json.loads(line.decode('utf-8'))
                        if message.get('type') == 'progress':
                            self._set('updating', '正在应用课程更新，请稍候。', candidate=public, step=message.get('step', ''))
                        elif message.get('type') == 'result':
                            result = message['result']
                        elif message.get('type') == 'error':
                            failure = message
                    code = child.wait()
                    if code == 0 and result is not None:
                        safe_result = dict(old_version=result['old_version'], new_version=result['new_version'],
                                           new_cards=[{'code': c['code'], 'title': c['title']} for c in result['new_cards']])
                        final = ('success', '课程更新完成。', {'result': safe_result})
                    else:
                        text = public_error((failure or {}).get('message', '更新进程异常结束；请重新打开学习台恢复未完成事务。'))
                        if failure and failure.get('restored'):
                            text += ' 内容已恢复原样。'
                        final = ('error', text, {})
        except Exception as exc:
            final = ('error', '更新未完成：' + public_error(exc), {})
        finally:
            # Drain both a failed decoder's remaining pipe and the process. Never
            # wait while holding the status lock: UI/heartbeats must remain live.
            child = self.process
            try:
                if child is not None:
                    try:
                        if child.poll() is None:
                            child.communicate()
                    finally:
                        try:
                            if child.stdout is not None:
                                child.stdout.close()
                        finally:
                            # Do not reopen writes while a transaction is alive,
                            # even when draining its output failed.
                            child.wait()
            except (OSError, ValueError) as exc:
                final = ('error', '更新进程收尾失败：' + public_error(exc), {})
            finally:
                with self.guard:
                    self.process = None
                    self.lifecycle.finish_update()
                    self._set(*final[:2], **final[2])
