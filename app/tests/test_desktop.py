import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.desktop import launch
from app.tests.helpers import symlink_or_skip


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learning-hub desktop 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for relative in ("app/server/main.py", "app/static/index.html", "content/course.yaml"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        self.ready = {"port": 8765, "url": "http://127.0.0.1:8765", "pid": 123,
                      "project_id": launch.project_id(self.root), "instance_id": "unique", "existing": False}
        self.health = dict(self.ready, ok=True, stopping=False)

    def test_health_identity_is_checked(self):
        self.assertEqual(launch.validate_ready(self.ready, self.root, self.health), "http://127.0.0.1:8765")
        for field in ("pid", "project_id", "instance_id"):
            with self.subTest(field=field), self.assertRaises(launch.LaunchError):
                launch.validate_ready(self.ready, self.root, dict(self.health, **{field: "other"}))

    def test_ready_cannot_redirect_requests(self):
        for changes in ({"port": True}, {"port": 80}, {"port": "8765"},
                        {"url": "https://example.com"}, {"url": "http://localhost:8765"},
                        {"project_id": "another"}, {"pid": True}, {"pid": 0}, {"instance_id": None}):
            with self.subTest(changes=changes), self.assertRaises(launch.LaunchError):
                launch.validate_ready(dict(self.ready, **changes), self.root, self.health)

    def test_stopping_service_is_not_loaded(self):
        with self.assertRaises(launch.LaunchError):
            launch.validate_ready(self.ready, self.root, dict(self.health, stopping=True))

    def test_readiness_accepts_existing_instance(self):
        target = self.root / "ready.json"
        target.write_text(json.dumps(dict(self.ready, existing=True)), encoding="utf-8")
        child = mock.Mock(); child.poll.return_value = 0
        with mock.patch.object(launch, "fetch_json", return_value=self.health) as fetch:
            ready = launch.wait_ready(child, target, self.root)
        self.assertTrue(ready["existing"])
        fetch.assert_called_once_with("http://127.0.0.1:8765/api/health")

    def test_readiness_fails_if_child_failed(self):
        child = mock.Mock(); child.poll.return_value = 2
        with self.assertRaises(launch.LaunchError):
            launch.wait_ready(child, self.root / "missing.json", self.root)

    def test_profile_stable_per_project_and_edge_app_arguments(self):
        args = launch.edge_arguments("edge.exe", self.ready["url"], self.root, self.root / "userdata")
        self.assertIn("--app=http://127.0.0.1:8765", args)
        self.assertTrue(any("--user-data-dir=" in part and launch.project_id(self.root) in part for part in args))
        self.assertNotEqual(launch.project_id(self.root), launch.project_id(self.root / "other"))
        self.assertNotIn("--disable-web-security", args)

    def test_find_edge_machine_and_user_install(self):
        edge = self.root / "local" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        edge.parent.mkdir(parents=True); edge.touch()
        self.assertEqual(launch.find_edge({"LOCALAPPDATA": str(self.root / "local")}), edge)
        with self.assertRaises(launch.LaunchError):
            launch.find_edge({})

    def test_windows_starts_hidden_and_removes_only_own_receipt(self):
        child = mock.Mock(); child.poll.return_value = None
        def ready(process, path, root):
            path.write_text("{}", encoding="utf-8")
            return self.ready
        with mock.patch.dict(launch.os.environ, {"LOCALAPPDATA": str(self.root / "profile")}), \
             mock.patch.object(launch.subprocess, "Popen", return_value=child) as popen, \
             mock.patch.object(launch, "wait_ready", side_effect=ready), \
             mock.patch.object(launch, "hidden_process_kwargs", return_value={"creationflags": 0x08000000}):
            launch.start_windows(self.root, edge=Path("edge.exe"))
        calls = popen.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("--desktop", calls[0].args[0])
        self.assertEqual(calls[0].kwargs["creationflags"], 0x08000000)
        self.assertEqual(calls[1].kwargs["creationflags"], 0x08000000)
        self.assertEqual(list((self.root / "logs/runtime").glob("launch-*.json")), [])
        child.terminate.assert_not_called()

    def test_logs_keep_ten_managed_files_and_preserve_user_log(self):
        folder = self.root / "logs"; folder.mkdir()
        for i in range(15):
            (folder / ("desktop-%03d.log" % i)).touch()
        (folder / "my-notes.log").write_text("keep", encoding="utf-8")
        launch.log_file(self.root)
        self.assertEqual(len(list(folder.glob("desktop-*.log"))), 10)
        self.assertEqual((folder / "my-notes.log").read_text(encoding='utf-8'), "keep")

    def test_logs_link_is_rejected(self):
        other = self.root / "outside"; other.mkdir()
        symlink_or_skip(self, self.root / "logs", other, directory=True)
        with self.assertRaises(ValueError):
            launch.log_file(self.root)
        self.assertEqual(list(other.iterdir()), [])

    def test_project_parent_link_is_rejected(self):
        (self.root / "app").rename(self.root / "actual-app")
        symlink_or_skip(self, self.root / "app", self.root / "actual-app", directory=True)
        with self.assertRaises(ValueError):
            launch.validate_project(self.root)

    def test_failed_edge_launch_does_not_kill_recovery_process(self):
        child = mock.Mock(); child.poll.return_value = None
        def receipt(process, path, root):
            path.write_text(json.dumps(self.ready), encoding="utf-8")
            return self.ready
        with mock.patch.dict(launch.os.environ, {"LOCALAPPDATA": str(self.root / "profile")}), \
             mock.patch.object(launch.subprocess, "Popen", side_effect=[child, OSError("edge failed")]), \
             mock.patch.object(launch, "wait_ready", side_effect=receipt):
            with self.assertRaises(OSError):
                launch.start_windows(self.root, edge=Path("edge.exe"))
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_install_scripts_disable_python_auto_download_and_use_utf8_bom(self):
        for name in ("install.ps1", "launch.ps1"):
            path = Path(__file__).resolve().parents[1] / "windows" / name
            source = path.read_bytes()
            self.assertTrue(source.startswith(b"\xef\xbb\xbf"))
            self.assertIn("PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'", source.decode("utf-8-sig"))
            self.assertNotIn("Set-ExecutionPolicy", source.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
