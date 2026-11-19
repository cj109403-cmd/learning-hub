"""Durable, explicitly resolved drafts. Never expire or delete unresolved input.

One live draft per card+field: an older version from another window is moved to
drafts/archive/ (kept, never deleted) when a newer one arrives or at startup.

Each client/card/field has a monotonic watermark. A confirmed/discarded draft leaves
that watermark behind, so a late HTTP PUT cannot resurrect an older version.
"""
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from .progress import (_atomic_write, _assert_regular, _decode, _ensure_directory,
                       _json_bytes, local_now, StorageError)


# Fields that record where the user is, not what they typed. Never kept as drafts.
STATE_FIELDS = ("position",)


class DraftError(StorageError):
    status = 400


class DraftStore:
    def __init__(self, progress_dir):
        supplied = Path(progress_dir)
        if not supplied.is_absolute():
            raise ValueError("DraftStore 必须显式传入绝对路径。")
        self.directory = supplied.parent.resolve() / supplied.name / "drafts"
        self._thread_lock = threading.RLock()

    @contextmanager
    def _lock(self):
        # Runtime storage only: the updater never constructs this store.
        from app.platform_support import file_lock
        with self._thread_lock:
            _ensure_directory(self.directory)
            lock = self.directory / ".write.lock"
            _assert_regular(lock)
            descriptor = os.open(str(lock), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                with file_lock(descriptor):
                    yield
            finally:
                os.close(descriptor)

    @staticmethod
    def _identity(payload):
        client = payload.get("client_id")
        card, field = payload.get("card"), payload.get("field")
        if not isinstance(client, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", client):
            raise DraftError("草稿客户端标识不正确。")
        if not isinstance(card, str) or not re.fullmatch(r"(?:[a-z0-9-]{1,100}|@settings)", card):
            raise DraftError("草稿卡片标识不正确。")
        if not isinstance(field, str) or not re.fullmatch(r"(?:summary|writeup|position|settings|answer:[a-zA-Z0-9_-]{1,100}|check:[a-zA-Z0-9_-]{1,100})", field):
            raise DraftError("草稿字段不正确。")
        if (field == "settings") != (card == "@settings"):
            raise DraftError("设置草稿位置不正确。")
        return hashlib.sha256(json.dumps([client, card, field]).encode()).hexdigest()

    @staticmethod
    def _sequence(value):
        if type(value) is not int or not 1 <= value <= 2 ** 53 - 1:
            raise DraftError("草稿序号不正确。")
        return value

    def _path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise DraftError("草稿标识不正确。")
        return self.directory / (identifier + ".json")

    def _read(self, path):
        _assert_regular(path)
        if not path.exists():
            return None
        try:
            envelope = _decode(path.read_bytes())
            if envelope.get("version") != 1 or type(envelope.get("sequence")) is not int:
                raise ValueError("unknown envelope")
            entry = envelope.get("draft")
            if entry is not None:
                if self._identity(entry) != path.stem or entry["id"] != path.stem or entry["sequence"] != envelope["sequence"]:
                    raise ValueError("identity mismatch")
                self._sequence(entry["sequence"])
                if not isinstance(entry.get("text"), str):
                    raise ValueError("invalid text")
            return envelope
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise StorageError("有一份草稿无法读取，原件已保留，请先请 Codex 协助恢复：" + path.name) from exc

    @staticmethod
    def _tombstone(envelope):
        return {"version": 1, "sequence": envelope["sequence"], "draft": None,
                "text_sha256": hashlib.sha256(envelope["draft"]["text"].encode("utf-8")).hexdigest()}

    def _archive_locked(self, path, envelope):
        """Move a superseded draft into drafts/archive/ (never deleted) and leave a
        tombstone so a late request cannot bring the old version back."""
        archive = self.directory / "archive"
        _ensure_directory(archive)
        name = "%s-%s.json" % (path.stem, local_now().replace(":", "").replace("+", "p").replace("-", ""))
        target = archive / name
        _assert_regular(target)
        _atomic_write(target, _json_bytes(envelope))
        _atomic_write(path, _json_bytes(self._tombstone(envelope)))

    def _live_locked(self):
        rows = []
        for path in sorted(self.directory.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                continue
            try:
                envelope = self._read(path)
            except StorageError:
                continue
            if envelope and envelope.get("draft"):
                rows.append((path, envelope))
        return rows

    def consolidate(self, progress, settings=None):
        """Tidy drafts left by earlier windows: those identical to the record are resolved,
        and for each card+field only the newest stays; older versions go to drafts/archive/."""
        resolved = archived = state = 0
        with self._lock():
            groups = {}
            for path, envelope in self._live_locked():
                entry = envelope["draft"]
                if entry["field"] in STATE_FIELDS:
                    # Reading position is program state: cleared, not protected (CODEX_BRIEF 18.8).
                    _atomic_write(path, _json_bytes(self._tombstone(envelope)))
                    state += 1
                elif self.matches_record(entry, progress, settings):
                    _atomic_write(path, _json_bytes(self._tombstone(envelope)))
                    resolved += 1
                else:
                    groups.setdefault((entry["card"], entry["field"]), []).append((path, envelope))
            for rows in groups.values():
                rows.sort(key=lambda row: (row[1]["draft"].get("at", ""), row[1]["sequence"]))
                for path, envelope in rows[:-1]:
                    self._archive_locked(path, envelope)
                    archived += 1
        return {"resolved": resolved, "archived": archived, "state": state}

    def put(self, payload, progress=None, settings=None):
        identifier = self._identity(payload)
        sequence = self._sequence(payload.get("sequence"))
        text = payload.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 512000:
            raise DraftError("草稿文字过长或格式不正确。")
        baseline = payload.get("baseline")
        if baseline is not None and (not isinstance(baseline, str) or len(baseline.encode("utf-8")) > 512000):
            raise DraftError("草稿基线不正确。")
        entry = {key: payload[key] for key in ("client_id", "card", "field", "sequence", "text")}
        entry.update(id=identifier, at=local_now(), baseline=baseline)
        with self._lock():
            path = self._path(identifier)
            old = self._read(path)
            if entry["field"] in STATE_FIELDS:
                # Program state is never a draft (CODEX_BRIEF 18.8); older clients may still send it.
                if old and old.get("draft"):
                    _atomic_write(path, _json_bytes(self._tombstone(old)))
                return dict(entry, resolved=True)
            if old and sequence <= old["sequence"]:
                # Same sequence is idempotent only for identical data. Older data
                # is rejected rather than falsely acknowledged as protected.
                previous = old.get("draft")
                if previous is None and sequence == old["sequence"] and old.get("text_sha256") == hashlib.sha256(text.encode("utf-8")).hexdigest():
                    _atomic_write(path, _json_bytes(old))
                    return dict(entry, resolved=True)
                if previous and sequence == old["sequence"] and previous["text"] == text:
                    _atomic_write(path, _json_bytes(old))
                    return deepcopy(previous)
                raise DraftError("草稿已有更新的版本，请保留当前页面并重试。")
            envelope = {"version": 1, "sequence": sequence, "draft": entry}
            if progress is not None and self.matches_record(entry, progress, settings):
                # Identical to what is already saved: nothing to protect.
                _atomic_write(path, _json_bytes(self._tombstone(envelope)))
                return dict(entry, resolved=True)
            _atomic_write(path, _json_bytes(envelope))
            for other, older in self._live_locked():
                draft = older["draft"]
                if other != path and draft["card"] == entry["card"] and draft["field"] == entry["field"]:
                    self._archive_locked(other, older)
        return entry

    def list(self):
        entries, notices = [], []
        with self._lock():
            for path in sorted(self.directory.glob("*.json")):
                if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                    notices.append("发现无法识别的草稿文件，已原样保留：" + path.name)
                    continue
                try:
                    envelope = self._read(path)
                    if envelope and envelope.get("draft"):
                        entries.append(deepcopy(envelope["draft"]))
                except StorageError as exc:
                    notices.append(str(exc))
        return {"drafts": sorted(entries, key=lambda row: (row["at"], row["id"])), "notices": notices}

    def _resolve(self, payload, predicate):
        path = self._path(payload.get("id"))
        sequence = self._sequence(payload.get("sequence"))
        with self._lock():
            envelope = self._read(path)
            if not envelope or not envelope.get("draft"):
                return False
            if sequence != envelope["sequence"] or not predicate(envelope["draft"]):
                return False
            _atomic_write(path, _json_bytes({"version": 1, "sequence": sequence, "draft": None, "text_sha256": hashlib.sha256(envelope["draft"]["text"].encode("utf-8")).hexdigest()}))
            return True

    def discard(self, payload):
        """Called only by the explicit per-draft discard API action."""
        return self._resolve(payload, lambda entry: True)

    @staticmethod
    def matches_record(entry, progress, settings=None):
        """Whether this draft's text is exactly what the record (or settings) already holds."""
        record = progress.get("cards", {}).get(entry["card"], {})
        field, text = entry["field"], entry["text"]
        if field == "summary":
            return "summary" in record and record["summary"] == text
        if field == "writeup":
            return record.get("writeup", {}).get("text") == text
        if field.startswith("answer:"):
            return record.get("questions", {}).get(field[7:], {}).get("my_answer") == text
        if field.startswith("check:"):
            attempts = record.get("checks", {}).get(field[6:], {}).get("attempts", [])
            return bool(attempts) and attempts[-1].get("value") == text
        try:
            value = json.loads(text)
        except ValueError:
            return False
        if field == "settings":
            return isinstance(value, dict) and bool(value) and settings is not None and all(settings.get(k) == v for k, v in value.items())
        if field == "position":
            actual = record.get("ui", {})
            return isinstance(value, dict) and value.get("phase") == actual.get("phase") and value.get("anchor", "") == actual.get("anchor", "") and (not value.get("learn_completed") or actual.get("learn_completed") is True)
        return False

    def confirm(self, payload, progress, settings=None):
        """Trust only server-read records, never a client-supplied 'saved' flag."""
        def matches(entry):
            return self.matches_record(entry, progress, settings)
        return self._resolve(payload, matches)
