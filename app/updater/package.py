"""Inspect every ZIP entry and all filesystem references before replacement."""
import hashlib
import io
import json
import re
import stat
import sys
import zipfile
from pathlib import Path

from app.file_safety import (CONTENT_ROOTS, FILE_ROOTS, ID_PATTERN, REQUIRED_ROOTS,
                             ContentSafetyError, content_relative, path_key,
                             safe_relative, assert_no_symlinks)

VENDOR = Path(__file__).absolute().parents[1] / "vendor_py"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))
import yaml

MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_ENTRIES = 10000


def _mapping(data, label):
    if not isinstance(data, dict):
        raise ContentSafetyError(label + " 必须是对象")
    return data


def _list(data, label):
    if not isinstance(data, list):
        raise ContentSafetyError(label + " 必须是列表")
    return data


def _identifier(value, label):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ContentSafetyError(label + " 含不安全的 id")
    return value


def load_yaml(data, label):
    try:
        return _mapping(yaml.safe_load(data.decode("utf-8")), label)
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise ContentSafetyError(label + " 的 YAML 无法读取") from exc


def preflight_references(files):
    """The trusted validator builds paths from these fields. Bound them first."""
    course = load_yaml(files["content/course.yaml"], "course.yaml")
    if type(course.get("spec_version")) is not int or course["spec_version"] != 1:
        raise ContentSafetyError("这份更新使用当前程序不支持的课程格式，请先请 Codex 更新程序；现有内容保持不动。")
    cards = {}
    for track in _list(course.get("tracks"), "tracks"):
        track = _mapping(track, "track")
        tid = _identifier(track.get("id"), "track.id")
        for index, cid in enumerate(_list(track.get("cards"), "track.cards"), 1):
            _identifier(cid, "card.id")
            path = "content/cards/{}/{}.md".format(tid, cid)
            if path not in files:
                raise ContentSafetyError("更新包缺少卡片：" + path)
            try:
                text = files[path].decode("utf-8")
            except UnicodeError as exc:
                raise ContentSafetyError("卡片不是 UTF-8：" + path) from exc
            # Invalid lesson structure is left for the original validator;
            # no path is formed from an unreadable front matter document.
            if not text.startswith("---\n") or "\n---\n" not in text[4:]:
                continue
            header = text[4:].split("\n---\n", 1)[0]
            fm = load_yaml(header.encode("utf-8"), path)
            _identifier(fm.get("id"), path + " id")
            for dependency in _list(fm.get("requires", []), "requires"):
                _identifier(dependency, "requires")
            for item in _list(fm.get("data") or [], "data"):
                item = _mapping(item, "data item")
                relative = safe_relative(item.get("file"))
                data_path = "data/tasks/{}/{}".format(cid, relative.as_posix())
                if data_path not in files:
                    raise ContentSafetyError("更新包缺少数据文件：" + data_path)
            cards[cid] = {"id": cid, "code": "{}{}".format(track.get("label", ""), index),
                          "title": str(fm.get("title", cid))}
    return course, cards


class UpdatePackage:
    def __init__(self, filename):
        filename = Path(filename)
        if not re.fullmatch(r"learning-hub-update-[^/\\]+\.zip", filename.name):
            raise ContentSafetyError("请选择 learning-hub-update-*.zip 更新包")
        self.filename = filename
        self.files = {}
        self.entries = []
        self.directories = set()
        seen = {}
        total = 0
        try:
            assert_no_symlinks(filename.absolute().parent, filename.name, allow_missing=False)
            # Hash and parse exactly the same immutable bytes, even if a sender
            # replaces the downloaded file after the user reviewed it.
            with filename.open('rb') as handle:
                raw_bytes = handle.read(MAX_TOTAL + 16 * 1024 * 1024 + 1)
            if len(raw_bytes) > MAX_TOTAL + 16 * 1024 * 1024:
                raise ContentSafetyError("更新包压缩文件超过大小限制")
            self.fingerprint = hashlib.sha256(raw_bytes).hexdigest()
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ENTRIES:
                    raise ContentSafetyError("更新包文件数量过多")
                for info in infos:
                    if info.orig_filename != info.filename:
                        raise ContentSafetyError("更新包路径含 NUL 或不可见截断字符")
                    raw = info.filename
                    directory = info.is_dir()
                    name = raw[:-1] if directory else raw
                    content_relative(name)
                    key = path_key(name)
                    if key in seen:
                        raise ContentSafetyError("更新包包含重复或大小写冲突的路径：" + name)
                    seen[key] = directory
                    mode = stat.S_IFMT(info.external_attr >> 16)
                    if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise ContentSafetyError("更新包不能包含符号链接或特殊文件：" + name)
                    if (mode == stat.S_IFDIR and not directory) or (directory and mode == stat.S_IFREG):
                        raise ContentSafetyError("文件类型和路径不一致：" + name)
                    if name in FILE_ROOTS and directory:
                        raise ContentSafetyError("此路径必须是文件：" + name)
                    if name in CONTENT_ROOTS and name not in FILE_ROOTS and not directory:
                        raise ContentSafetyError("此路径必须是文件夹：" + name)
                    if info.flag_bits & 1:
                        raise ContentSafetyError("不支持加密更新包")
                    total += info.file_size
                    if info.file_size > MAX_FILE or total > MAX_TOTAL:
                        raise ContentSafetyError("更新包超过大小限制")
                    self.entries.append(raw)
                # Check ancestors using normalized names too, before extraction.
                aliases = {}
                for raw in self.entries:
                    name = raw.rstrip("/")
                    parts = name.split("/")
                    for n in range(1, len(parts) + 1):
                        ancestor = "/".join(parts[:n])
                        key = path_key(ancestor)
                        if key in aliases and aliases[key] != ancestor:
                            raise ContentSafetyError("更新包包含 Unicode 或大小写冲突：" + name)
                        aliases[key] = ancestor
                        if n < len(parts) and key in seen and seen[key] is False:
                            raise ContentSafetyError("文件和目录路径冲突：" + name)
                for info in infos:
                    name = info.filename.rstrip("/")
                    if info.is_dir():
                        self.directories.add(name)
                    else:
                        self.files[name] = archive.read(info)
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ContentSafetyError("无法读取更新包：" + str(exc)) from exc
        self.roots = set()
        for allowed in CONTENT_ROOTS:
            if allowed in FILE_ROOTS:
                present = allowed in self.files
            else:
                present = allowed in self.directories or any(
                    name.startswith(allowed + "/") for name in self.files)
            if present:
                self.roots.add(allowed)
            elif allowed in REQUIRED_ROOTS:
                raise ContentSafetyError("更新包缺少完整路径：" + allowed)
        if "content/course.yaml" not in self.files:
            raise ContentSafetyError("更新包缺少：content/course.yaml")
        self.course, self.cards = preflight_references(self.files)

    def extract(self, destination):
        destination = Path(destination)
        for name in sorted(self.directories):
            destination.joinpath(*safe_relative(name).parts).mkdir(parents=True, exist_ok=True)
        for name, data in self.files.items():
            target = destination.joinpath(*safe_relative(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            # The destination is freshly created and never supplied by the ZIP.
            with target.open("xb") as handle:
                handle.write(data)
