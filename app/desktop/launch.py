"""Windows pythonw entry point and independently testable launch protocol.

Never searches a whole project. The service alone owns learning records; the
launcher reads its unique readiness receipt and verifies a local health reply.
"""
import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
import uuid

from app.platform_support import hidden_process_kwargs
from app.file_safety import assert_no_symlinks


class LaunchError(Exception):
    pass


def project_id(root):
    return hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]


def validate_project(root):
    root = Path(root).resolve()
    for relative in ("app/server/main.py", "app/static/index.html", "content/course.yaml"):
        if not assert_no_symlinks(root, relative).is_file():
            raise LaunchError("这个文件夹不是完整的学习台。请重新解压分享包，再运行安装脚本。")
    return root


def log_file(root):
    folder = assert_no_symlinks(root, "logs")
    folder.mkdir(exist_ok=True)
    name = "desktop-{}-{}.log".format(datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8])
    target = folder / name
    target.touch(exist_ok=False)
    previous = sorted((p for p in folder.glob("*.log") if p.name.startswith(("desktop-", "learning-hub-"))
                       and p.is_file() and not p.is_symlink()),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for old in previous[10:]:
        try:
            old.unlink()
        except OSError:
            pass  # Another live launcher may still have its log open on Windows.
    return target


def fetch_json(url, *, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Learning-Control"] = token
    request = urllib.request.Request(url, data=b"{}" if token else None, headers=headers)
    # Do not route a loopback readiness check through the user's proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=1) as response:
        return json.loads(response.read(65536))


def validate_ready(ready, root, health):
    if not isinstance(ready, dict) or not isinstance(health, dict):
        raise LaunchError("后台启动信息不完整，请打开日志检查。")
    port = ready.get("port")
    if type(port) is not int or not 8765 <= port <= 8775:
        raise LaunchError("后台返回了无效端口。")
    expected_url = "http://127.0.0.1:{}".format(port)
    if ready.get("url", "").rstrip("/") != expected_url:
        raise LaunchError("后台地址不是学习台的本机地址。")
    if ready.get("project_id") != project_id(root):
        raise LaunchError("这个后台属于另一个项目，已停止连接。")
    if type(ready.get("pid")) is not int or ready["pid"] <= 0 or not isinstance(ready.get("instance_id"), str) or not ready["instance_id"]:
        raise LaunchError("后台身份信息不完整。")
    for key in ("project_id", "instance_id", "pid"):
        if not ready.get(key) or ready[key] != health.get(key):
            raise LaunchError("后台身份核对失败，已停止连接。")
    if health.get("ok") is not True or health.get("stopping"):
        raise LaunchError("后台正在退出，请稍后重新打开。")
    return expected_url


def wait_ready(process, ready_path, root, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            assert_no_symlinks(root, ready_path.relative_to(root).as_posix())
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            port = ready.get("port")
            # Validate routing before making any network request.
            if type(port) is not int or not 8765 <= port <= 8775:
                raise LaunchError("后台返回了无效端口。")
            health = fetch_json("http://127.0.0.1:{}/api/health".format(port))
            validate_ready(ready, root, health)
            return ready
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            if process.poll() not in (None, 0):
                raise LaunchError("学习台后台没有成功启动。可能端口都被占用，请打开日志查看原因。")
            time.sleep(0.05)
    raise LaunchError("后台启动超过 20 秒仍未就绪，请打开日志查看原因。")


def find_edge(environment=None):
    environment = os.environ if environment is None else environment
    for base in (environment.get("ProgramFiles(x86)"), environment.get("ProgramFiles"),
                 environment.get("LOCALAPPDATA")):
        if base:
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return candidate
    raise LaunchError("没有找到 Microsoft Edge。请先安装 Edge，再打开学习台。")


def edge_arguments(edge, url, root, local_app_data):
    profile = Path(local_app_data) / "LearningHub" / "Browser" / project_id(root)
    return [str(edge), "--app=" + url, "--user-data-dir=" + str(profile),
            "--no-first-run", "--no-default-browser-check", "--disable-background-mode"]


def start_windows(root, *, edge=None):
    root = validate_project(root)
    edge = edge or find_edge()
    local_data = os.environ.get("LOCALAPPDATA")
    if not local_data:
        raise LaunchError("无法找到 Windows 用户应用数据目录，请重新登录后重试。")
    log = log_file(root)
    runtime = assert_no_symlinks(root, "logs/runtime")
    runtime.mkdir(exist_ok=True)
    ready_path = runtime / ("launch-" + uuid.uuid4().hex + ".json")
    token = secrets.token_urlsafe(32)
    with log.open("ab", buffering=0) as output:
        child = subprocess.Popen([sys.executable, "-B", "-m", "app.server.main", "--root", str(root),
                                  "--desktop", "--ready-file", str(ready_path), "--token", token],
                                 cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 **hidden_process_kwargs())
        try:
            ready = wait_ready(child, ready_path, root)
            url = ready["url"].rstrip("/")
            subprocess.Popen(edge_arguments(edge, url, root, local_data), stdin=subprocess.DEVNULL,
                             stdout=output, stderr=output, **hidden_process_kwargs())
        except Exception:
            # Windows terminate() cannot deliver a graceful signal. Attempt
            # authenticated shutdown only if our own readiness receipt exists;
            # otherwise let the no-heartbeat timeout finish recovery safely.
            try:
                assert_no_symlinks(root, ready_path.relative_to(root).as_posix())
                candidate = json.loads(ready_path.read_text(encoding="utf-8"))
                if candidate.get("token") == token and not candidate.get("existing"):
                    port = candidate.get("port")
                    if type(port) is int and 8765 <= port <= 8775:
                        health = fetch_json("http://127.0.0.1:{}/api/health".format(port))
                        url = validate_ready(candidate, root, health)
                        fetch_json(url + "/api/shutdown", token=token)
            except (OSError, ValueError, LaunchError):
                pass
            raise
        finally:
            try:
                ready_path.unlink()
            except FileNotFoundError:
                pass
    return log


def main():
    root = Path(__file__).resolve().parents[2]
    if sys.platform != "win32":
        raise LaunchError("这个入口只用于 Windows；Mac 请打开学习台.app。")
    try:
        start_windows(root)
    except Exception as error:
        message = str(error) + "\n\n使用说明：app\\README.md\n日志：logs 文件夹"
        ctypes.windll.user32.MessageBoxW(None, message, "学习台无法打开", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
