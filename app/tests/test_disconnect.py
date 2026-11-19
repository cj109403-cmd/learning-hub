"""Disconnected response writes must not undo saves or mask storage failures."""
from contextlib import redirect_stderr
import errno
import http.client
import io
import json
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.server.main import start_server
from app.server.progress import StorageError
from app.tests.test_content import isolated_course


class DisconnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-disconnect-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        isolated_course(self.root)
        self.server = start_server(self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, payload=None):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            client.request(method, path, body, {'Content-Type': 'application/json'})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def op(self, **payload):
        payload.setdefault('revision', self.server.hub.store.read()['revision'])
        return self.request('POST', '/api/op', payload)

    def disconnected_request(self, payload, error, fail_at, expected_status):
        body = json.dumps(payload).encode()
        address = self.server.server_address
        raw = (f'POST /api/op HTTP/1.1\r\nHost: {address[0]}:{address[1]}\r\n'
               f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n'
               'Connection: close\r\n\r\n').encode() + body
        writes = []
        original = socketserver._SocketWriter.write

        def disconnect(writer, data):
            writes.append(bytes(data))
            if len(writes) >= fail_at:
                raise error(10053, 'injected closed response socket')
            return original(writer, data)

        stderr = io.StringIO()
        with redirect_stderr(stderr), patch.object(socketserver._SocketWriter, 'write', disconnect):
            with socket.create_connection(address, timeout=5) as client:
                client.sendall(raw)
                while client.recv(65536):
                    pass
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(len(writes), fail_at, 'must not retry the dead socket with a 500 response')
        self.assertTrue(writes[0].startswith(f'HTTP/1.1 {expected_status} '.encode()))
        self.assertEqual(self.server.hub.lifecycle.snapshot()['active'], 0)

    def test_disconnect_during_successful_save_keeps_record_and_server_usable(self):
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            for fail_at in (1, 2):
                with self.subTest(error=error.__name__, fail_at=fail_at):
                    summary = f'{error.__name__}-{fail_at}'
                    self.disconnected_request(dict(op='save_summary', card='lesson-one', text=summary,
                                                   revision=self.server.hub.store.read()['revision']),
                                              error, fail_at, 200)
                    status, snapshot = self.request('GET', '/api/bootstrap')
                    self.assertEqual(status, 200)
                    self.assertEqual(snapshot['progress']['cards']['lesson-one']['summary'], summary)
                    self.assertEqual(self.op(op='save_summary', card='lesson-one', text='next save')[0], 200)

    def test_disconnect_while_reporting_conflict_keeps_original_record(self):
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            for fail_at in (1, 2):
                with self.subTest(error=error.__name__, fail_at=fail_at):
                    before = self.server.hub.store.path.read_bytes()
                    self.disconnected_request(dict(op='save_summary', card='lesson-one', text='stale',
                                                   revision='stale-revision'), error, fail_at, 409)
                    self.assertEqual(self.server.hub.store.path.read_bytes(), before)
                    self.assertEqual(self.request('GET', '/api/health')[0], 200)
                    self.assertEqual(self.op(op='save_summary', card='lesson-one', text='fresh')[0], 200)

    def test_disconnect_during_business_error_does_not_change_record(self):
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                before = self.server.hub.store.path.read_bytes()
                self.disconnected_request(dict(op='start_card', card='task-one',
                                               revision=self.server.hub.store.read()['revision']),
                                          ConnectionAbortedError, fail_at, 403)
                self.assertEqual(self.server.hub.store.path.read_bytes(), before)
                self.assertEqual(self.request('GET', '/api/health')[0], 200)

    def test_storage_and_other_io_errors_still_return_failure(self):
        for error, status in ((StorageError('disk unavailable'), 503),
                              (OSError(errno.ENOSPC, 'disk full'), 500)):
            with self.subTest(error=type(error).__name__):
                before = self.server.hub.store.path.read_bytes()
                with patch.object(self.server.hub.store, 'mutate', side_effect=error):
                    actual, body = self.op(op='save_summary', card='lesson-one', text='not saved')
                self.assertEqual(actual, status)
                self.assertIn('error', body)
                self.assertEqual(self.server.hub.store.path.read_bytes(), before)
                self.assertEqual(self.op(op='save_summary', card='lesson-one', text='saved later')[0], 200)

    def split_request(self, path, headers=None, body=b'{}', *, delay=.025):
        address = self.server.server_address
        fields = {'Host': f'{address[0]}:{address[1]}', 'Content-Type': 'application/json',
                  'Content-Length': str(len(body)) if body is not None else '2',
                  'Connection': 'close'}
        fields.update(headers or {})
        raw = ('POST ' + path + ' HTTP/1.1\r\n' +
               ''.join(key + ': ' + value + '\r\n' for key, value in fields.items()) + '\r\n').encode()
        with socket.create_connection(address, timeout=3) as client:
            client.sendall(raw)
            if body is not None:
                time.sleep(delay)
                client.sendall(body)
            response = http.client.HTTPResponse(client)
            response.begin()
            return response.status, json.loads(response.read())

    def test_split_rejected_requests_deliver_errors_without_executing_operations(self):
        before = self.server.hub.store.path.read_bytes()
        cases = [(path, {}, 403) for path in
                 ('/api/heartbeat', '/api/shutdown', '/api/updates/check', '/api/drafts')]
        cases += [('/api/op', {'Host': 'outside.invalid'}, 403),
                  ('/api/op', {'Origin': 'https://outside.invalid'}, 403),
                  ('/api/op', {'Sec-Fetch-Site': 'cross-site'}, 403),
                  ('/api/op', {'X-Learning-Session': 'invalid!session'}, 400),
                  ('/api/op', {'Content-Type': 'text/plain'}, 415)]
        with patch.object(self.server.hub, 'api', side_effect=AssertionError('rejected request ran business logic')) as api:
            for path, headers, expected in cases:
                with self.subTest(path=path, headers=headers):
                    status, response = self.split_request(path, headers)
                    self.assertEqual(status, expected)
                    self.assertIn('error', response)
            api.assert_not_called()
        self.assertEqual(self.server.hub.store.path.read_bytes(), before)
        self.assertEqual(self.request('GET', '/api/health')[0], 200)

    def test_rejected_missing_or_huge_request_body_has_bounded_wait(self):
        with patch.object(self.server.hub, 'api', side_effect=AssertionError('unauthorized operation')) as api:
            for length in ('2', '1000000', '1000001', '999999999999999999999999', 'invalid'):
                with self.subTest(length=length):
                    started = time.monotonic()
                    status, response = self.split_request('/api/shutdown', {'Content-Length': length}, body=None)
                    self.assertEqual(status, 403)
                    self.assertIn('error', response)
                    self.assertLess(time.monotonic() - started, 2, 'refusal must not wait for the normal 10-second read timeout')
            api.assert_not_called()
        self.assertFalse(self.server.hub.lifecycle.snapshot()['stopping'])


if __name__ == '__main__':
    unittest.main()
