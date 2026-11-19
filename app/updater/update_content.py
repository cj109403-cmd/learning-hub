"""Double-click/terminal entry point. No progress or workspace paths are used."""
import argparse
import json
import tempfile
from datetime import datetime
import os
from pathlib import Path
import sys

# Running the updater must not create Python cache files in app/ either.
sys.dont_write_bytecode = True

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).absolute().parents[2]))

from app.platform_support import downloads_directory

from app.file_safety import (ContentSafetyError, absolute_root,
                             assert_no_symlinks)
from app.updater.package import UpdatePackage, load_yaml
from app.updater.transaction import (content_lock, recover_locked, recover_pending,
                                     replace_locked)
from app.updater.content_check import check_content
import subprocess


def _old_course(root):
    path = assert_no_symlinks(root, "content/course.yaml")
    if not path.is_file():
        return {"content_version": "无", "tracks": []}
    return load_yaml(path.read_bytes(), "现有 course.yaml")


def apply_update(root, zip_path, *, on_step=None, expected_sha256=None,
                 expected_content_version=None):
    root = absolute_root(root)
    with content_lock(root, exclusive=True):
        recovered = recover_locked(root)
        package = UpdatePackage(zip_path)
        if expected_sha256 is not None and package.fingerprint != expected_sha256:
            raise ContentSafetyError("更新包在检查后发生变化，请重新检查更新。")
        old = _old_course(root)
        if expected_content_version is not None and str(old.get("content_version", "未知")) != expected_content_version:
            raise ContentSafetyError("课程版本在检查后发生变化，请重新检查更新；现有内容保持不动。")
        old_ids = {cid for track in old.get("tracks", []) if isinstance(track, dict)
                   for cid in track.get("cards", []) if isinstance(cid, str)}
        result = replace_locked(root, package, check_content, on_step)
        result.update(old_version=str(old.get("content_version", "未知")),
                      new_version=str(package.course.get("content_version", "未知")),
                      new_cards=[card for cid, card in package.cards.items() if cid not in old_ids],
                      recovered_previous_update=recovered, entries=package.entries)
        return result


def inspect_update(root, zip_path):
    """Fully check in a disposable root; never replace the current content."""
    root = absolute_root(root)
    package = UpdatePackage(zip_path)
    with content_lock(root):
        old = _old_course(root)
    old_ids = {cid for track in old.get("tracks", []) if isinstance(track, dict)
               for cid in track.get("cards", []) if isinstance(cid, str)}
    with tempfile.TemporaryDirectory(prefix="learning-hub-update-check-") as directory:
        candidate = Path(directory)
        package.extract(candidate)
        report = check_content(candidate)
    if report["code"] != 0:
        raise ContentSafetyError("内容校验未通过，现有内容保持不动。\n" + report["output"])
    return {"old_version": str(old.get("content_version", "未知")),
            "new_version": str(package.course.get("content_version", "未知")),
            "new_cards": [card for cid, card in package.cards.items() if cid not in old_ids],
            "fingerprint": package.fingerprint, "entries": package.entries,
            "validation_output": report["output"]}


def _worker(args, output):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    def emit(value):
        output(json.dumps(value, ensure_ascii=False))
        try:
            sys.stdout.flush()
        except (AttributeError, OSError):
            pass
    try:
        if not args.zip or not args.expected_sha256 or not args.expected_content_version:
            raise ContentSafetyError("后台更新必须提供更新包、已确认的文件指纹和当前课程版本。")
        result = apply_update(args.root, args.zip, expected_sha256=args.expected_sha256,
                              expected_content_version=args.expected_content_version,
                              on_step=lambda step: emit({"type": "progress", "step": step}))
        emit({"type": "result", "result": result})
        return 0
    except (ContentSafetyError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        emit({"type": "error", "message": str(exc), "restored": getattr(exc, "restored", False)})
        return 1
    except KeyboardInterrupt:
        emit({"type": "error", "message": "更新被中止，请重新启动学习台检查事务恢复。", "restored": False})
        return 130


def newest_download(downloads):
    candidates = []
    # The search is deliberately only Downloads' top level, never recursive.
    with os.scandir(downloads) as entries:
        for entry in entries:
            if (entry.name.startswith("learning-hub-update-") and entry.name.endswith(".zip")
                    and entry.is_file(follow_symlinks=False)):
                candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name, Path(entry.path)))
    if not candidates:
        raise ContentSafetyError("下载文件夹里没有 learning-hub-update-*.zip。请先把更新包放到“下载”，再双击更新。")
    return max(candidates)[2]


def main(argv=None, *, input_fn=input, output=print):
    parser = argparse.ArgumentParser(description="安全更新学习工作台的课程内容")
    parser.add_argument("zip", nargs="?", help="也可以把 ZIP 拖进终端")
    parser.add_argument("--root", type=Path, default=Path(__file__).absolute().parents[2],
                        help="项目路径；自动测试使用临时项目")
    parser.add_argument("--non-interactive", action="store_true", help="应用内更新使用的 JSON 进度模式")
    parser.add_argument("--expected-sha256", help="用户检查过的完整更新包指纹")
    parser.add_argument("--expected-content-version", help="用户检查时的当前课程版本，防止并发更新覆盖")
    args = parser.parse_args(argv)
    if args.non_interactive:
        return _worker(args, output)
    try:
        if recover_pending(args.root):
            output("已处理上次中断的内容更新。原学习记录不受影响。")
        package_path = Path(args.zip).expanduser().absolute() if args.zip else newest_download(downloads_directory())
        if package_path.is_symlink():
            raise ContentSafetyError("更新包不能是符号链接")
        modified = datetime.fromtimestamp(package_path.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        output("更新包：" + package_path.name)
        output("修改时间：" + modified)
        # Inspection is read-only. Display every path before confirmation.
        package = UpdatePackage(package_path)
        output("包内文件：\n" + "\n".join("  " + name for name in package.entries))
        answer = input_fn("应用这份内容更新？输入 y 确认，n 取消：").strip().lower()
        if answer != "y":
            output("已取消，现有内容保持不动。")
            return 0
        result = apply_update(args.root, package_path)
        output(result["validation_output"].strip())
        output("内容版本：{} → {}".format(result["old_version"], result["new_version"]))
        if result["new_cards"]:
            output("新增卡片：\n" + "\n".join("  {code} {title}".format(**card) for card in result["new_cards"]))
        else:
            output("本次没有新增卡片。")
        output("备份位置：" + result["backup"])
        output("更新完成。刷新浏览器页面即可。")
        return 0
    except (ContentSafetyError, OSError, ValueError, EOFError, subprocess.TimeoutExpired) as exc:
        output("更新未完成：" + str(exc))
        return 1
    except KeyboardInterrupt:
        output("已中止更新。若内容替换已经开始，原版本已恢复；请保留备份。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
