"""Built-in check of a new course before it replaces the current one.

The program checks a course exactly the way it will read it later: with its own
loader (app/server/content.py). Nothing that arrives inside an update package is
ever executed. tools/validate_content.py stays the course authors' own, stricter
checklist; it is not run by the updater.

The report names cards and problems only (fixed messages from the loader), never
answers or YAML excerpts, so it is safe to show on a learning page.
"""
from pathlib import Path

from app.server.content import content_path, load_content, plain, validate_course
from app.updater.package import yaml


def check_content(root):
    """Return {"code": 0 or 1, "output": text} for the course stored under root."""
    root = Path(root)
    try:
        raw = content_path(root, "content/course.yaml").read_text(encoding="utf-8")
        validate_course(plain(yaml.safe_load(raw)))
    except ValueError as error:
        return {"code": 1, "output": "课程总表 content/course.yaml 不符合格式：" + str(error)}
    except (OSError, yaml.YAMLError, TypeError, RecursionError):
        return {"code": 1, "output": "课程总表 content/course.yaml 无法读取或解析。"}
    snapshot = load_content(root)
    if snapshot.issues:
        lines = ["{}：{}".format(issue["card"] or "课程总表", issue["message"]) for issue in snapshot.issues]
        return {"code": 1, "output": "内容检查：{} 张卡可以读取，{} 个错误。\n{}".format(
            len(snapshot.cards), len(lines), "\n".join(lines))}
    return {"code": 0, "output": "内容检查：{} 张卡全部可以读取，0 个错误。".format(len(snapshot.cards))}
