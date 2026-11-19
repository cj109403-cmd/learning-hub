"""Shared, deliberately narrow path checks; never search the project root."""
import os
import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath


class ContentSafetyError(ValueError):
    pass


CONTENT_ROOTS = (
    "content", "data", "docs", "tools", "HANDOFF.md", "AGENTS.md",
    ".agents/skills/learning-ui",
)
FILE_ROOTS = {"HANDOFF.md", "AGENTS.md"}
# Every update package carries the course itself. The other roots (the authors' notes,
# checker and design spec) are replaced only when a package includes them.
REQUIRED_ROOTS = ("content", "data")
WINDOWS_DEVICES = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"} | {
    prefix + str(i) for prefix in ("com", "lpt") for i in range(1, 10)
} | {prefix + digit for prefix in ("com", "lpt") for digit in "¹²³"}


def is_reparse_point(info):
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def safe_relative(value):
    """Reject ambiguous path spellings before any filesystem operation."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ContentSafetyError("文件路径不合法")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ContentSafetyError("文件路径含控制字符")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or value.startswith("/"):
        raise ContentSafetyError("文件路径越界或含歧义")
    if any(part.endswith((".", " ")) or part.split(".", 1)[0].rstrip(" ").casefold() in WINDOWS_DEVICES
           or any(char in part for char in '<>"|?*') for part in parts):
        raise ContentSafetyError("文件路径不兼容 Windows 或含设备保留名")
    return PurePosixPath(*parts)


def path_key(value):
    # macOS commonly uses a case-insensitive, Unicode-normalizing filesystem.
    return unicodedata.normalize("NFC", str(value)).casefold()


def content_relative(value):
    path = safe_relative(value)
    name = path.as_posix()
    if not any(name == allowed or (allowed not in FILE_ROOTS and
               name.startswith(allowed + "/")) for allowed in CONTENT_ROOTS):
        raise ContentSafetyError("更新包包含未获准的路径：" + name)
    return path


def absolute_root(root):
    # abspath is lexical. resolve() would follow symlinks before we reject them.
    return Path(os.path.abspath(os.fspath(root)))


def assert_no_symlinks(root, relative="", *, allow_missing=True):
    """Only lstat the explicitly supplied root and its target's parent chain."""
    root = absolute_root(root)
    parts = safe_relative(relative).parts if relative else ()
    paths = [root]
    paths.extend(root.joinpath(*parts[:index]) for index in range(1, len(parts) + 1))
    for index, path in enumerate(paths):
        try:
            info = path.lstat()
            mode = info.st_mode
        except FileNotFoundError:
            if allow_missing:
                continue
            raise ContentSafetyError("文件不存在：" + str(path))
        if stat.S_ISLNK(mode) or is_reparse_point(info):
            raise ContentSafetyError("不能使用符号链接：" + str(path))
        if index < len(paths) - 1 and not stat.S_ISDIR(mode):
            raise ContentSafetyError("路径的上一级不是文件夹：" + str(path))
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ContentSafetyError("不能使用特殊文件：" + str(path))
    return root.joinpath(*parts)


def assert_regular_tree(root, relative):
    """Walk a named subtree only; reject links before descending into them."""
    start = assert_no_symlinks(root, str(relative))
    try:
        mode = start.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        return
    stack = [start]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                mode = info.st_mode
                if stat.S_ISLNK(mode) or is_reparse_point(info) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ContentSafetyError("内容目录包含链接或特殊文件：" + entry.path)
                if stat.S_ISDIR(mode):
                    stack.append(Path(entry.path))
