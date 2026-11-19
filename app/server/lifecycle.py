"""One gate coordinates accepted requests, content transactions and shutdown.

No filesystem access here. A heartbeat cannot save input the server never received.
"""
from contextlib import contextmanager
import threading
import time


class LifecycleError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class Lifecycle:
    # Browser and Windows pages keep the service alive with heartbeats. Hidden windows are
    # throttled or paused by the OS, so the fallback is generous; a native shell passes
    # timeout=None and ends the service itself (see main.py --parent-pid).
    def __init__(self, *, clock=time.monotonic, timeout=30 * 60):
        self.clock = clock
        self.timeout = timeout
        self.condition = threading.Condition(threading.RLock())
        self.last_heartbeat = clock()
        self.active = 0
        self.updating = False
        self.stopping = False
        self.stop_requested = False
        self.stop_reason = ''

    def heartbeat(self):
        with self.condition:
            if self.stopping:
                raise LifecycleError('学习台正在关闭，请重新打开。')
            self.last_heartbeat = self.clock()
            return {'ok': True, 'updating': self.updating, 'stopping': self.stopping}

    @contextmanager
    def operation(self):
        with self.condition:
            if self.stopping or self.stop_requested:
                raise LifecycleError('学习台正在关闭，尚未保存的草稿会保留。')
            if self.updating:
                raise LifecycleError('课程正在更新，请稍候；尚未保存的草稿会保留。')
            self.active += 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def reserve_update(self):
        with self.condition:
            if self.stopping or self.stop_requested:
                raise LifecycleError('学习台正在关闭。')
            if self.updating:
                raise LifecycleError('已有更新正在进行。', 409)
            # Close the admission gate BEFORE waiting for earlier writers.
            self.updating = True

    def wait_drained(self):
        with self.condition:
            while self.active:
                self.condition.wait(.2)

    def finish_update(self):
        with self.condition:
            self.updating = False
            self.condition.notify_all()

    def request_stop(self, *, defer_transaction=False, reason='shutdown requested by the window'):
        with self.condition:
            if self.updating and not defer_transaction:
                raise LifecycleError('课程正在更新，完成或恢复原内容后才能退出。', 409)
            self.stop_requested = True
            self.stop_reason = self.stop_reason or reason

    def should_stop(self):
        with self.condition:
            if self.stopping:
                return True
            silent = self.clock() - self.last_heartbeat
            expired = self.timeout is not None and silent >= self.timeout
            if not self.active and not self.updating and (self.stop_requested or expired):
                self.stopping = True
                if not self.stop_requested:
                    self.stop_reason = 'no heartbeat for %d seconds' % silent
                return True
            return False

    def snapshot(self):
        with self.condition:
            return {'updating': self.updating, 'stopping': self.stopping,
                    'active': self.active}
