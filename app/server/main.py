"""Local-only standard-library HTTP server. Pass an explicit temporary root in tests."""
import argparse
import hashlib
from contextlib import contextmanager
import json
import mimetypes
import os
import secrets
import signal
import time
import logging
from pathlib import Path, PurePosixPath
import socket
import socketserver
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit, parse_qs
import uuid
import webbrowser

from .content import load_content, content_path
from .progress import ProgressStore, StorageError, ConflictError, DEFAULT_SETTINGS
from . import rules, views, export
from app.updater.transaction import content_lock, PendingUpdateError
from app.file_safety import ContentSafetyError, assert_no_symlinks
from app.platform_support import file_lock, process_alive, reveal_file
from .lifecycle import Lifecycle, LifecycleError
from .drafts import DraftStore
from .updates import UpdateManager


class LearningHub:
    def __init__(self, root, *, lifecycle=None, token=None, downloads=None):
        self.root = Path(root).resolve()
        self.store = ProgressStore(self.root / 'progress')
        self.store.startup()
        self.session_id = uuid.uuid4().hex
        self.visits = {}
        self.guard = threading.RLock()
        self.lifecycle = lifecycle or Lifecycle()
        self.token = token or secrets.token_urlsafe(32)
        self.instance_id = uuid.uuid4().hex
        self.project_id = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        self.drafts = DraftStore(self.root / 'progress')
        self.updates = UpdateManager(self.root, self.lifecycle, downloads=downloads)

    def visit_context(self, session):
        return self.visits.get(session or self.session_id, {})

    def bootstrap(self, content, snapshot=None, session=None):
        snapshot = snapshot or self.store.read()
        if any(issue.get('card') is None for issue in content.issues):
            snapshot = dict(snapshot, read_only=True, notices=snapshot['notices'] + ['课程总表无法读取，学习记录暂时保持原样。请修复内容后刷新。'])
        progress = snapshot['progress']
        # A future schema is shown only when its old fields remain readable.
        try:
            cards = views.card_list(content, progress)
        except (KeyError, TypeError, ValueError, AttributeError):
            if not snapshot['read_only']:
                raise
            cards = []
        return dict(snapshot, course=views.public_course(content), cards=cards,
                    settings=self.store.get_settings(), issues=content.issues,
                    project_id=hashlib.sha256(str(self.root).encode()).hexdigest()[:16],
                    session_id=session or self.session_id, needs_welcome=not progress.get('welcome_seen_at'),
                    new_cards=progress.get('pending_new_cards', []),
                    desktop={'control_token': self.token, 'platform': platform_name(), 'heartbeat_seconds': 15})

    def api(self, method, path, payload=None, query=None, session=None):
        payload = payload or {}
        if path == '/api/health' and method == 'GET':
            return dict(ok=True, project_id=self.project_id, instance_id=self.instance_id,
                        pid=os.getpid(), platform=platform_name(), **self.lifecycle.snapshot())
        if path == '/api/heartbeat' and method == 'POST':
            return self.lifecycle.heartbeat()
        if path == '/api/shutdown' and method == 'POST':
            self.lifecycle.request_stop()
            return {'stopping': True}
        if path == '/api/updates/status' and method == 'GET':
            return self.updates.status()
        if path == '/api/updates/check' and method == 'POST':
            return self.updates.check()
        if path == '/api/updates/apply' and method == 'POST':
            return self.updates.apply(payload.get('candidate_id'))
        try:
            required = bool(self.store.get_settings().get('require_summary'))
        except StorageError:
            required = False
        token = rules.SUMMARY_REQUIRED.set(required)
        try:
            return self._guarded_api(method, path, payload, query, session)
        finally:
            rules.SUMMARY_REQUIRED.reset(token)

    def _guarded_api(self, method, path, payload=None, query=None, session=None):
        with self.lifecycle.operation():
            if path == '/api/drafts' and method == 'GET':
                return self.drafts.list()
            if path == '/api/drafts' and method == 'POST':
                with self.guard:
                    snapshot = self.store.read()
                    return {'draft': self.drafts.put(payload, snapshot['progress'], self.store.get_settings())}
            if path == '/api/drafts/discard' and method == 'POST':
                return {'discarded': self.drafts.discard(payload)}
            if path == '/api/drafts/confirm' and method == 'POST':
                with self.guard:
                    snapshot = self.store.read()
                    return {'confirmed': self.drafts.confirm(payload, snapshot['progress'], self.store.get_settings())}
            return self._learning_api(method, path, payload, query, session)

    def _learning_api(self, method, path, payload=None, query=None, session=None):
        payload = payload or {}
        query = query or {}
        with self.guard, content_lock(self.root):
            content = load_content(self.root)
            snapshot = self.store.read()
            progress = snapshot['progress']
            visit = self.visit_context(session)
            if path == '/api/bootstrap' and method == 'GET':
                return self.bootstrap(content, snapshot, session)
            if path == '/api/settings':
                if method == 'GET':
                    return self.store.get_settings()
                if method == 'PUT':
                    return self.store.put_settings(payload)
            if path == '/api/op' and method == 'POST':
                if content.issues and payload.get('op') not in ('seen_card', 'visit', 'welcome_done') and payload.get('card') not in content.cards:
                    raise rules.RuleError('这张卡的内容暂时无法读取，请查看页面顶部提示。', 422)
                snapshot, result = self.store.mutate(payload.get('revision'), lambda p: rules.perform(content, p, payload),
                                                     check_revision=rules.needs_revision(payload))
                if payload.get('op') == 'visit':
                    self.visits[session or self.session_id] = result
                if payload.get('op') == 'complete':
                    result = views.completion(content, snapshot['progress'], payload['card'], self.visit_context(session))
                return dict(self.bootstrap(content, snapshot, session), op=payload.get('op'), result=result)
            if path.startswith('/api/card/') and method == 'GET':
                cid = path[len('/api/card/'):]
                if cid not in content.cards:
                    raise rules.RuleError('没有找到这张卡，内容可能已更新。', 404)
                return {'card': views.public_card(content, content.cards[cid], progress), 'revision': snapshot['revision']}
            if path == '/api/today' and method == 'GET':
                return views.today(content, progress, visit)
            if path == '/api/route' and method == 'GET':
                return views.route(content, progress, visit)
            if path == '/api/lab' and method == 'GET':
                return views.lab(content, progress)
            if path == '/api/journal' and method == 'GET':
                return views.journal(content, progress, query.get('view', ['timeline'])[0], query.get('track', [''])[0])
            if path == '/api/search-index' and method == 'GET':
                return views.search_index(content, progress)
            if path == '/api/export' and method == 'POST':
                if payload.get('kind') == 'record':
                    if snapshot['read_only']:
                        raise StorageError('记录处于保护状态，请先恢复记录后再导出。')
                    return export.save_record(self.store.directory, content, progress)
                return {'text': export.ai_text(content, progress, payload)}
            if path == '/api/reveal' and method == 'POST':
                card = content.cards.get(payload.get('card'))
                filename = payload.get('file')
                if not card or rules.status(card, progress) == 'locked' or not isinstance(filename, str) or filename not in [f['file'] for f in card.get('data', [])]:
                    raise rules.RuleError('只能显示这张已解锁任务卡列出的数据文件。', 403)
                relative = 'data/tasks/' + card['id'] + '/' + filename
                try:
                    path = content_path(self.root, relative)
                    if not path.is_file():
                        raise ValueError()
                except (ValueError, OSError):
                    raise rules.RuleError('数据文件路径不安全或文件不存在。', 403)
                reveal_file(path)
                return {'revealed': filename}
            raise rules.RuleError('没有找到这个页面或操作。', 404)


class HubHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True
    # Reloading opens a burst of module requests. Windows can refuse them when
    # the standard library's five-connection accept queue fills briefly.
    request_queue_size = 32

    def server_bind(self):
        # Avoid reverse DNS on an offline Mac during local startup.
        socketserver.TCPServer.server_bind(self)
        self.server_name = '127.0.0.1'
        self.server_port = self.server_address[1]

    def __init__(self, address, hub):
        if address[0] != '127.0.0.1':
            raise ValueError('服务只能监听本机 127.0.0.1。')
        self.hub = hub
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'LearningHub/3'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        # Do not write answers or user-input query strings to the terminal log.
        pass

    def _send(self, status, body, mime='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        if status >= 400:
            self._discard_rejected_body()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src https://www.youtube-nocookie.com https://player.bilibili.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        try:
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A page may close after its save has committed. This also covers
            # sending an error response: never retry a closed socket with 500.
            self.close_connection = True

    def _discard_rejected_body(self):
        # On Windows, closing with unread request bytes can reset the socket
        # before the client receives its 403. Discard only bounded, declared
        # bodies; do not parse them or run an operation for a rejected request.
        if self.command not in ('POST', 'PUT') or getattr(self, '_body_consumed', False):
            return
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
            return
        try:
            remaining = int(lengths[0])
        except ValueError:
            return
        if not 0 < remaining <= 1000000:
            return
        timeout = self.connection.gettimeout()
        deadline = time.monotonic() + .25
        try:
            while remaining:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    break
                self.connection.settimeout(budget)
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except (OSError, ValueError):
            pass  # Only this socket discard failed; still attempt the error response.
        finally:
            self._body_consumed = True
            self.connection.settimeout(timeout)

    def _check_local(self):
        port = self.server.server_address[1]
        host = self.headers.get('Host', '')
        allowed = {'127.0.0.1:%s' % port, 'localhost:%s' % port}
        if host not in allowed:
            raise rules.RuleError('请从本机启动窗口给出的地址打开学习台。', 403)
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + h for h in allowed}:
            raise rules.RuleError('拒绝来自其他网站的操作。', 403)
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise rules.RuleError('拒绝来自其他网站的操作。', 403)

    def _body(self):
        if self.headers.get_content_type() != 'application/json':
            raise rules.RuleError('操作必须使用 JSON 格式。', 415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise rules.RuleError('请求长度不正确。')
        if not 0 < length <= 1000000:
            self.close_connection = True
            raise rules.RuleError('请求过大或没有内容。', 413)
        try:
            self._body_consumed = True
            value = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeError):
            raise rules.RuleError('请求不是有效的 JSON。')
        if not isinstance(value, dict):
            raise rules.RuleError('请求必须是一个对象。')
        return value

    def _handle(self):
        try:
            self._check_local()
            url = urlsplit(self.path)
            path = unquote(url.path)
            if self.command in ('GET', 'HEAD') and not path.startswith('/api/'):
                self._static(path)
                return
            if self.command in ('POST', 'PUT') and is_control(path):
                supplied = self.headers.get('X-Learning-Control', '')
                if not secrets.compare_digest(supplied, self.server.hub.token):
                    raise rules.RuleError('桌面会话已改变，请重新打开页面。', 403)
            session = self.headers.get('X-Learning-Session')
            if session and (len(session) > 80 or not session.replace('-', '').isalnum()):
                raise rules.RuleError('会话编号不正确。')
            payload = self._body() if self.command in ('POST', 'PUT') else None
            result = self.server.hub.api(self.command, path, payload, parse_qs(url.query), session)
            self._send(200, result)
        except ConflictError as exc:
            self._send(409, {'error': 'conflict', 'message': str(exc), 'revision': exc.revision})
        except (rules.RuleError, StorageError, ContentSafetyError, LifecycleError) as exc:
            self._send(getattr(exc, 'status', 503), {'error': 'request_failed', 'message': str(exc), 'updating': self.server.hub.lifecycle.snapshot()['updating']})
        except (ValueError, TypeError):
            self._send(400, {'error': 'invalid_input', 'message': '输入格式不正确，请检查后再试。'})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception:
            self._send(500, {'error': 'internal_error', 'message': '这次操作没有完成。请保留草稿，打开日志文件夹或请 Codex 检查；程序不会显示标准答案。'})

    def _static(self, path):
        hub = self.server.hub
        if path == '/':
            with hub.lifecycle.operation():
                try:
                    target = assert_no_symlinks(hub.root, 'app/static/index.html', allow_missing=False)
                except ContentSafetyError:
                    raise rules.RuleError('这个文件路径不安全。', 403)
                html = target.read_text(encoding='utf-8')
                settings = json.dumps(hub.store.get_settings(), ensure_ascii=True).replace('<', '\\u003c')
            self._send(200, html.replace('{{INITIAL_SETTINGS}}', settings), 'text/html; charset=utf-8')
            return
        if not (path.startswith('/static/') or path.startswith('/vendor/')) or '\\' in path or '\x00' in path:
            raise rules.RuleError('没有找到这个文件。', 404)
        relative = path.lstrip('/')
        pure = PurePosixPath(relative)
        if str(pure) != relative or '..' in pure.parts:
            raise rules.RuleError('没有找到这个文件。', 404)
        try:
            target = assert_no_symlinks(hub.root, 'app/' + relative)
        except ContentSafetyError:
            raise rules.RuleError('这个文件路径不安全。', 403)
        if not target.is_file():
            raise rules.RuleError('没有找到这个文件。', 404)
        mime = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        if target.suffix == '.js':
            mime = 'text/javascript'
        self._send(200, target.read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text/') else ''))

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PUT = _handle


def platform_name():
    return 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else 'other'


def is_control(path):
    return path in ('/api/heartbeat', '/api/shutdown') or path.startswith(('/api/drafts', '/api/updates'))


def start_server(root, port=None, **hub_options):
    # Bind first. A failed or duplicate start must not create record backups.
    ports = [port] if port is not None else range(8765, 8776)
    for candidate in ports:
        try:
            server = HubHTTPServer(('127.0.0.1', candidate), None)
        except OSError as exc:
            if candidate == (port if port is not None else 8775):
                raise OSError('没有找到空闲端口（8765–8775），请关闭旧的学习台窗口后再试。') from exc
            continue
        try:
            server.hub = LearningHub(root, **hub_options)
            return server
        except Exception:
            server.server_close()
            raise
    raise RuntimeError('无法启动本机服务。')


def runtime_directory(root):
    for name in ('logs', 'logs/runtime'):
        path = assert_no_symlinks(root, name)
        path.mkdir(exist_ok=True, mode=0o700)
    return path


def write_registration(path, value):
    from .progress import _atomic_write, _json_bytes
    _atomic_write(path, _json_bytes(value))


def validate_registration(path, project_id):
    from urllib.request import build_opener, ProxyHandler
    try:
        assert_no_symlinks(path.parent, path.name, allow_missing=False)
        value = json.loads(path.read_text(encoding='utf-8'))
        port = value['port']
        if type(port) is not int or not 1 <= port <= 65535 or value['project_id'] != project_id:
            return None
        url = 'http://127.0.0.1:%s' % port
        with build_opener(ProxyHandler({})).open(url + '/api/health', timeout=1) as response:
            health = json.load(response)
        if (health.get('ok') is True and not health.get('stopping')
                and all(health.get(key) == value[key] for key in ('project_id', 'instance_id', 'pid'))):
            value['url'] = url
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def configure_log(root):
    from datetime import datetime
    folder = assert_no_symlinks(root, 'logs')
    name = 'learning-hub-' + datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f') + '.log'
    path = folder / name
    logger = logging.getLogger('learning-hub-' + uuid.uuid4().hex)
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    candidates = sorted((p for p in folder.glob('*.log') if p.name.startswith(('learning-hub-', 'desktop-')) and not p.is_symlink() and p.is_file()), key=lambda p: p.stat().st_mtime_ns)
    for old in candidates[:-10]:
        try:
            old.unlink()
        except OSError:
            # A currently running shell can still hold this file on Windows.
            # A later launch retries retention; startup remains usable.
            pass
    return logger, handler


def main(argv=None):
    parser = argparse.ArgumentParser(description='本地学习台')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--port', type=int)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--token')
    parser.add_argument('--parent-pid', type=int, help='native shell process; the service follows it instead of heartbeats')
    args = parser.parse_args(argv)
    server = None
    logger = handler = None
    descriptor = None
    lock = None
    registration = None
    ready = None
    old_signals = {}
    try:
        root = args.root.resolve()
        runtime = runtime_directory(root)
        registration = assert_no_symlinks(root, 'logs/runtime/server.json')
        ready = args.ready_file.absolute() if args.ready_file else registration
        if ready.parent.resolve() != runtime.resolve() or ready.is_symlink():
            raise ValueError('启动登记必须放在项目的 logs/runtime 文件夹里。')
        # Validate a caller-supplied basename without traversing anything else.
        assert_no_symlinks(root, 'logs/runtime/' + ready.name)
        project_id = hashlib.sha256(str(root).encode()).hexdigest()[:16]
        lockpath = assert_no_symlinks(root, 'logs/runtime/server.lock')
        descriptor = os.open(str(lockpath), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        lock = file_lock(descriptor, blocking=False)
        try:
            lock.__enter__()
        except (BlockingIOError, OSError):
            lock = None
            existing = None
            for unused in range(40):
                existing = validate_registration(registration, project_id)
                if existing:
                    break
                time.sleep(.1)
            if not existing:
                raise OSError('另一个学习台正在启动或关闭，请稍后再试。')
            existing['existing'] = True
            if ready != registration:
                write_registration(ready, existing)
            print(json.dumps({k: v for k, v in existing.items() if k != 'token'}, ensure_ascii=False), flush=True)
            if args.open:
                webbrowser.open(existing['url'])
            return 0
        logger, handler = configure_log(root)
        logger.info('service starting')
        # Recovery only uses the updater whitelist; before any learning API starts.
        from app.updater.transaction import recover_pending
        recover_pending(root)
        server = start_server(root, args.port, token=args.token)
        hub = server.hub
        try:
            tidy = hub.drafts.consolidate(hub.store.read()['progress'], hub.store.get_settings())
            if tidy['resolved'] or tidy['archived'] or tidy['state']:
                logger.info('drafts tidied: %(resolved)s already saved, %(archived)s older versions moved to progress/drafts/archive, %(state)s reading-position records cleared', tidy)
        except StorageError as exc:
            logger.error('draft tidy skipped: %s', type(exc).__name__)
        if args.parent_pid:
            hub.lifecycle.timeout = None
            logger.info('service follows window process %s', args.parent_pid)
        url = 'http://127.0.0.1:%s' % server.server_address[1]
        value = dict(pid=os.getpid(), port=server.server_address[1], url=url,
                     project_id=hub.project_id, instance_id=hub.instance_id,
                     token=hub.token, existing=False)
        write_registration(registration, value)
        if ready != registration:
            write_registration(ready, value)
        print(json.dumps({k: v for k, v in value.items() if k != 'token'}, ensure_ascii=False) if args.desktop else '学习台已启动：' + url, flush=True)
        if args.open:
            webbrowser.open(url)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, name='http-listener')
        worker.start()
        def stop_signal(signum, frame):
            hub.lifecycle.request_stop(defer_transaction=True, reason='signal %s' % signum)
        for number in (signal.SIGINT, signal.SIGTERM):
            old_signals[number] = signal.signal(number, stop_signal)
        checked = 0.0
        while not hub.lifecycle.should_stop():
            time.sleep(.1)
            if args.parent_pid and time.monotonic() - checked >= 2:
                checked = time.monotonic()
                if not process_alive(args.parent_pid, parent=True):
                    hub.lifecycle.request_stop(defer_transaction=True, reason='window process %s exited' % args.parent_pid)
        logger.info('service stopping: %s', hub.lifecycle.stop_reason or 'unknown')
        server.shutdown()
        worker.join()
        server.server_close()
        logger.info('service stopped cleanly')
        return 0
    except (OSError, ValueError, ContentSafetyError, StorageError, LifecycleError) as exc:
        if logger:
            logger.error('startup/shutdown failed: %s', type(exc).__name__)
        print('启动没有完成：' + str(exc), file=sys.stderr)
        return 1
    except BaseException as exc:
        if logger:
            logger.error('service stopped by an unexpected error: %s', type(exc).__name__)
        raise
    finally:
        for number, previous in old_signals.items():
            signal.signal(number, previous)
        if server is not None:
            server.server_close()
            try:
                current = json.loads(registration.read_text(encoding='utf-8'))
                if current.get('instance_id') == server.hub.instance_id:
                    registration.unlink()
            except (OSError, ValueError):
                pass
        if handler:
            handler.close()
            logger.removeHandler(handler)
        if lock:
            lock.__exit__(None, None, None)
        if descriptor is not None:
            os.close(descriptor)


if __name__ == '__main__':
    raise SystemExit(main())
