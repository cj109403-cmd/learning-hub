"""Real loopback coverage for browser connection bursts; no project files."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import threading
from types import SimpleNamespace
import unittest

from app.server.main import HubHTTPServer


class HTTPBacklogTests(unittest.TestCase):
    def test_browser_connection_burst_survives_a_busy_accept_loop(self):
        hub = SimpleNamespace(api=lambda *args: {"ok": True})
        server = HubHTTPServer(("127.0.0.1", 0), hub)
        clients = []
        worker = None

        def connect(unused):
            client = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                client.connect()
                return client, None
            except OSError as error:
                client.close()
                return None, repr(error)

        try:
            # Hold the accept loop while browser connections arrive, as can
            # happen during thread scheduling or a quick page reload.
            with ThreadPoolExecutor(max_workers=16) as pool:
                outcomes = list(pool.map(connect, range(16)))
            clients = [client for client, error in outcomes if client is not None]
            self.assertEqual([error for client, error in outcomes if error], [])
            worker = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": .01}, daemon=True)
            worker.start()
            for client in clients:
                client.request("GET", "/api/health")
            for client in clients:
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"ok": True})
        finally:
            if worker:
                server.shutdown()
            for client in clients:
                client.close()
            server.server_close()
            if worker:
                worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
