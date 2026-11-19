"""Create a clean source sharing ZIP from an allowlist, never a root scan.

This is a local sharing artifact, not an open-source release. Private records,
workspace, logs, backups, Git history and review evidence are outside the
allowlist and are not even inspected by this module.
"""
import argparse
from datetime import datetime
import os
from pathlib import Path
import stat
import zipfile


TREES = (
    "content", "data", "docs", "tools", ".agents/skills/learning-ui",
    "app/server", "app/static", "app/updater", "app/vendor", "app/vendor_py",
    "app/desktop", "app/windows", "app/macos", "app/assets/icons", "app/tests",
)
FILES = (
    "AGENTS.md", "HANDOFF.md", "启动学习台.command", "更新内容.command",
    "app/__init__.py", "app/file_safety.py", "app/platform_support.py",
    "app/README.md", "app/ARCHITECTURE.md", "app/WINDOWS_TEST.md",
    "app/THIRD_PARTY_NOTICES.txt", "app/distribution/build_share.py",
    "app/distribution/windows/installation.txt", "app/distribution/windows/install.cmd",
)
WINDOWS_TREES = (
    "content", "data", "app/server", "app/static", "app/updater",
    "app/vendor", "app/vendor_py", "app/desktop", "app/windows",
)
WINDOWS_FILES = (
    "app/__init__.py", "app/file_safety.py", "app/platform_support.py",
    "app/THIRD_PARTY_NOTICES.txt", "tools/validate_content.py",
    "app/assets/icons/orange/learning-hub.ico",
    "app/distribution/windows/installation.txt", "app/distribution/windows/install.cmd",
)
# Settings that belong to this particular course travel with it when present
# (app/course_extras.json); the engine works without them.
OPTIONAL_FILES = ("app/course_extras.json",)
WINDOWS_ALIASES = {
    "app/distribution/windows/installation.txt": ("安装说明.txt", "app/README.md"),
    "app/distribution/windows/install.cmd": ("安装学习台.cmd",),
}
EXCLUDE_NAMES = frozenset(("__pycache__", ".DS_Store", "build", "dist", "screenshots",
                           "progress", "workspace", "logs", "backups", ".git"))


def safe_files(root, platform="all"):
    """Yield known roots only. A symlink/junction is rejected before traversal."""
    root = Path(root).resolve()
    if platform not in ("all", "windows"):
        raise ValueError("未知的分享平台。")
    files, trees = (WINDOWS_FILES, WINDOWS_TREES) if platform == "windows" else (FILES, TREES)

    def inspect(path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("分享清单中不允许链接：" + str(path.relative_to(root)))
        return info

    def tree(folder):
        if not stat.S_ISDIR(inspect(folder).st_mode):
            raise ValueError("分享清单需要普通文件夹：" + str(folder.relative_to(root)))
        # Only traverse explicit safe roots, never enumerate root itself.
        with os.scandir(folder) as entries:
            paths = sorted((Path(entry.path) for entry in entries
                            if entry.name not in EXCLUDE_NAMES and not entry.name.endswith(".pyc")))
        for path in paths:
            info = inspect(path)
            if stat.S_ISDIR(info.st_mode):
                yield from tree(path)
            elif stat.S_ISREG(info.st_mode):
                yield path
            else:
                raise ValueError("分享清单中包含特殊文件：" + str(path.relative_to(root)))

    for name in files:
        path = root / name
        for parent in reversed(Path(name).parents):
            if str(parent) != ".":
                inspect(root / parent)
        if not stat.S_ISREG(inspect(path).st_mode):
            raise ValueError("分享清单需要普通文件：" + name)
        yield path
    for name in OPTIONAL_FILES:
        path = root / name
        if os.path.lexists(path):
            if not stat.S_ISREG(inspect(path).st_mode):
                raise ValueError("分享清单需要普通文件：" + name)
            yield path
    for name in trees:
        # Check intermediate components too: .agents/skills must not be links.
        relative = Path(name)
        for parent in reversed(relative.parents):
            if str(parent) != ".":
                inspect(root / parent)
        yield from tree(root / name)


def build_share(root, output, platform="all"):
    root = Path(root).resolve()
    output = Path(output).absolute()
    try:
        relative = output.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None and relative.parts[:3] != ("app", "distribution", "dist"):
        raise ValueError("项目内的分享包只能保存到 app/distribution/dist。")
    if relative is not None:
        for count in range(1, len(relative.parts) + 1):
            path = root.joinpath(*relative.parts[:count])
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("分享包输出位置不允许链接。")
    # Build the complete allowlist first; do not leave a partial ZIP on errors.
    sources = list(safe_files(root, platform))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    count = 0
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in sources:
                relative = source.relative_to(root).as_posix()
                aliases = WINDOWS_ALIASES.get(relative) if platform == "windows" else None
                if aliases:
                    text = source.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
                    for alias in aliases:
                        encoding = "ascii" if alias.endswith(".cmd") else "utf-8-sig"
                        archive.writestr("learning-hub/" + alias, text.replace("\n", "\r\n").encode(encoding))
                        count += 1
                else:
                    archive.write(source, "learning-hub/" + relative)
                    count += 1
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def main():
    parser = argparse.ArgumentParser(description="从允许清单生成不带私人数据的学习台分享包")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--platform", choices=("all", "windows"), default="all",
                        help="windows 仅打包 Windows 运行文件和安装说明，排除开发交接文档")
    parser.add_argument("--name", default="learning-hub-share-" + datetime.now().strftime("%Y%m%d") + ".zip")
    args = parser.parse_args()
    if Path(args.name).name != args.name or not args.name.endswith(".zip"):
        parser.error("文件名必须是不含路径的 .zip 名称")
    target = args.root / "app" / "distribution" / "dist" / args.name
    count = build_share(args.root, target, args.platform)
    print("分享包已生成：{}（{} 个文件，不含学习记录）".format(target, count))


if __name__ == "__main__":
    main()
