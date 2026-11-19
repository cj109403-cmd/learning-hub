"""
学习工作台内容校验脚本（对应 docs/CONTENT_SPEC.md v0.8）。

用法（在 learning-hub 根目录）：
    python3 tools/validate_content.py
依赖：PyYAML（pip install pyyaml）
退出码：0 = 没有错误；1 = 有错误。警告不影响退出码。
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML，请先运行：pip install pyyaml")

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
CARDS = CONTENT / "cards"
DATA_TASKS = ROOT / "data" / "tasks"
GENERATORS = ROOT / "data" / "generators"

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TYPES = {"lesson", "task"}
RES_KINDS = {"video", "course", "practice", "reading"}
EMBEDS = {"youtube", "bilibili", "none"}
LANGS = {"en", "zh"}
Q_KINDS = {"concept", "predict", "apply"}
CHECK_KINDS = {"integer", "number", "text", "choice", "set"}
NORMALIZE = {"trim", "upper", "lower", "no_space"}
LESSON_SECTIONS = ["讲解", "怎么学"]
LESSON_OPTIONAL = ["延伸"]
TASK_SECTIONS = ["任务说明", "提示", "参考解法"]

errors, warnings = [], []


def err(where, msg):
    errors.append(f"[错误] {where}: {msg}")


def warn(where, msg):
    warnings.append(f"[警告] {where}: {msg}")


def split_front_matter(text):
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5:]


def h2_titles(body):
    titles, in_code = [], False
    for line in body.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("## "):
            titles.append(line[3:].strip())
    return titles


def section_text(body, title):
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    return m.group(1) if m else ""


def check_course_meta(course):
    """检查 course.yaml 里的 content_version、lab、people、welcome，返回人物 id 集合。"""
    w = "course.yaml"
    cv = course.get("content_version")
    if not isinstance(cv, str) or not re.match(r"^\d{4}-\d{2}-\d{2}\.\d+$", cv):
        err(w, f"content_version 应为 'YYYY-MM-DD.N' 格式的字符串，实际是 {cv!r}")
    lab = course.get("lab") or {}
    if not lab.get("name") or not isinstance(lab.get("fictional"), bool):
        err(w, "lab 需要 name 和 fictional（true/false）")
    people = course.get("people") or []
    ids = set()
    for i, p in enumerate(people):
        for f in ["id", "name", "role", "initial"]:
            if not p.get(f):
                err(f"{w} people[{i}]", f"缺少 {f}")
        if p.get("id") in ids:
            err(f"{w} people[{i}]", f"人物 id 重复：{p.get('id')!r}")
        ids.add(p.get("id"))
        if p.get("initial") and len(p["initial"]) != 1:
            err(f"{w} people[{i}]", "initial 应该是一个字")
    if not ids:
        err(w, "people 不能为空")
    wel = course.get("welcome") or {}
    if wel.get("from") not in ids or not wel.get("message"):
        err(w, "welcome 需要 from（people 里的 id）和 message")
    return ids


def check_date(where, value):
    if isinstance(value, dt.date):
        return
    try:
        dt.date.fromisoformat(str(value))
    except ValueError:
        err(where, f"verified 日期格式不对：{value!r}，应为 YYYY-MM-DD")


def check_resources(where, resources):
    if not isinstance(resources, list):
        err(where, "resources 必须是列表")
        return
    for i, r in enumerate(resources):
        w = f"{where} resources[{i}]"
        for f in ["kind", "title", "source", "url", "embed", "lang", "minutes", "required", "focus"]:
            if f not in r:
                err(w, f"缺少字段 {f}")
        if r.get("kind") not in RES_KINDS:
            err(w, f"kind 不合法：{r.get('kind')!r}")
        if r.get("embed") not in EMBEDS:
            err(w, f"embed 不合法：{r.get('embed')!r}")
        if r.get("lang") not in LANGS:
            err(w, f"lang 不合法：{r.get('lang')!r}")
        if not str(r.get("url", "")).startswith(("https://", "http://")):
            err(w, "url 必须以 http:// 或 https:// 开头")
        if r.get("embed") in {"youtube", "bilibili"} and r.get("kind") != "video":
            err(w, "只有 video 可以内嵌")
        if not isinstance(r.get("required"), bool):
            err(w, "required 必须是 true 或 false")
        if not isinstance(r.get("minutes"), int) or r.get("minutes", 0) <= 0:
            err(w, "minutes 必须是正整数")


def check_questions(where, questions):
    if not isinstance(questions, list) or not (3 <= len(questions) <= 5):
        err(where, "学习卡必须有 3–5 道自测题")
        return
    ids = [q.get("id") for q in questions]
    if len(set(ids)) != len(ids):
        err(where, "自测题 id 有重复")
    kinds = set()
    for i, q in enumerate(questions):
        w = f"{where} questions[{i}]"
        for f in ["id", "kind", "prompt", "answer"]:
            if not q.get(f):
                err(w, f"缺少或为空：{f}")
        if q.get("kind") not in Q_KINDS:
            err(w, f"kind 不合法：{q.get('kind')!r}")
        kinds.add(q.get("kind"))
    if not kinds & {"predict", "apply"}:
        err(where, "至少要有一道 predict 或 apply 题")


def same_value(kind, a, b):
    if kind == "set":
        return sorted(map(str, a)) == sorted(map(str, b))
    if kind == "number":
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return a == b


def check_task(where, fm, card_id, people):
    for f in ["from", "message", "simulated", "capstone", "checks"]:
        if f not in fm:
            err(where, f"任务卡缺少字段 {f}")
    if fm.get("from") not in people:
        err(where, f"from={fm.get('from')!r} 不在 course.yaml 的 people 里")
    if not isinstance(fm.get("simulated"), bool):
        err(where, "simulated 必须是 true 或 false")
    if fm.get("simulated") is False:
        src = fm.get("source") or {}
        for f in ["citation", "url", "license"]:
            if not src.get(f):
                err(where, f"真实数据必须写 source.{f}")
    for i, d in enumerate(fm.get("data") or []):
        p = DATA_TASKS / card_id / str(d.get("file", ""))
        if not d.get("file") or not p.is_file():
            err(f"{where} data[{i}]", f"文件不存在：{p.relative_to(ROOT)}")
        if not d.get("description"):
            err(f"{where} data[{i}]", "缺少 description")

    checks = fm.get("checks") or []
    if not checks:
        err(where, "任务卡至少要有 1 个 check")
    ids = [c.get("id") for c in checks]
    if len(set(ids)) != len(ids):
        err(where, "check id 有重复")

    answers_path = GENERATORS / f"{card_id}.answers.json"
    answers = None
    if answers_path.is_file():
        answers = json.loads(answers_path.read_text(encoding="utf-8")).get("checks", {})
    else:
        err(where, f"缺少标准答案文件：{answers_path.relative_to(ROOT)}")

    for i, c in enumerate(checks):
        w = f"{where} checks[{i}]"
        kind = c.get("kind")
        if not c.get("id") or not c.get("prompt"):
            err(w, "缺少 id 或 prompt")
        if kind not in CHECK_KINDS:
            err(w, f"kind 不合法：{kind!r}")
            continue
        if "expected" not in c:
            err(w, "缺少 expected")
            continue
        exp = c["expected"]
        if kind == "integer" and not (isinstance(exp, int) and not isinstance(exp, bool)):
            err(w, "integer 的 expected 必须是整数")
        if kind == "number":
            if not isinstance(exp, (int, float)) or isinstance(exp, bool):
                err(w, "number 的 expected 必须是数字")
            tol = c.get("tolerance")
            if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol < 0:
                err(w, "number 必须有非负的 tolerance")
        if kind == "text" and not isinstance(exp, str):
            err(w, "text 的 expected 必须是文字")
        if kind == "set" and not isinstance(exp, list):
            err(w, "set 的 expected 必须是列表")
        if kind == "choice":
            opts = c.get("options") or []
            opt_ids = [o.get("id") for o in opts]
            if len(opts) < 2:
                err(w, "choice 至少要有 2 个选项")
            if len(set(opt_ids)) != len(opt_ids):
                err(w, "choice 选项 id 有重复")
            if exp not in opt_ids:
                err(w, f"expected={exp!r} 不在选项里")
        for n in c.get("normalize") or []:
            if n not in NORMALIZE:
                err(w, f"normalize 不合法：{n!r}")
        if answers is not None:
            if c.get("id") not in answers:
                err(w, "标准答案文件里没有这个 check")
            elif not same_value(kind, exp, answers[c["id"]]):
                err(w, f"expected={exp!r} 与标准答案文件不一致（{answers[c['id']]!r}）")

    if "debrief" in fm and (not isinstance(fm["debrief"], str) or not fm["debrief"].strip()):
        err(where, "debrief 如果写了，必须是非空文字")

    if "writeup" in fm:
        wu = fm["writeup"] or {}
        if not wu.get("prompt"):
            err(where, "writeup 缺少 prompt")
        if not isinstance(wu.get("rubric"), list) or not wu.get("rubric"):
            err(where, "writeup 的 rubric 必须是非空列表")


def main():
    course_path = CONTENT / "course.yaml"
    if not course_path.is_file():
        err("course.yaml", "文件不存在")
        return
    course = yaml.safe_load(course_path.read_text(encoding="utf-8")) or {}
    people = check_course_meta(course)
    tracks = course.get("tracks") or []
    if not tracks:
        err("course.yaml", "没有任何线")

    all_ids, card_track, order = [], {}, {}
    track_ids = []
    for t in tracks:
        tid = t.get("id")
        for f in ["id", "label", "title", "summary", "cards"]:
            if not t.get(f):
                err(f"course.yaml 线 {tid}", f"缺少字段 {f}")
        track_ids.append(tid)
        order[tid] = list(t.get("cards") or [])
        for cid in order[tid]:
            all_ids.append(cid)
            card_track[cid] = tid
    if len(set(track_ids)) != len(track_ids):
        err("course.yaml", "线的 id 有重复")
    dup = {c for c in all_ids if all_ids.count(c) > 1}
    if dup:
        err("course.yaml", f"这些卡出现了不止一次：{sorted(dup)}")

    meta = {}
    for cid in dict.fromkeys(all_ids):
        tid = card_track[cid]
        where = f"{tid}/{cid}"
        if not ID_RE.match(cid):
            err(where, "id 只能用小写字母、数字、连字符")
        path = CARDS / tid / f"{cid}.md"
        if not path.is_file():
            err(where, f"卡片文件不存在：{path.relative_to(ROOT)}")
            continue
        fm_text, body = split_front_matter(path.read_text(encoding="utf-8"))
        if fm_text is None:
            err(where, "缺少 YAML 头信息（文件开头的 --- … ---）")
            continue
        try:
            fm = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError as e:
            err(where, f"YAML 解析失败：{e}")
            continue
        meta[cid] = fm
        if fm.get("id") != cid:
            err(where, f"头信息里的 id={fm.get('id')!r} 和文件名不一致")
        for f in ["type", "title", "subtitle", "minutes", "requires", "verified", "resources"]:
            if f not in fm:
                err(where, f"缺少字段 {f}")
        if fm.get("type") not in TYPES:
            err(where, f"type 不合法：{fm.get('type')!r}")
        # v0.8：参考要点（"结"阶段给学习者参考，可以一键用作自己的总结）
        tk = fm.get("takeaway")
        if tk is None:
            warn(where, "缺少 takeaway（参考要点）")
        elif not isinstance(tk, str) or not (10 <= len(tk.strip()) <= 120):
            err(where, "takeaway 必须是 10–120 个字的一段文字")
        if not isinstance(fm.get("minutes"), int) or fm.get("minutes", 0) <= 0:
            err(where, "minutes 必须是正整数")
        if not isinstance(fm.get("requires", []), list):
            err(where, "requires 必须是列表")
        check_date(where, fm.get("verified"))
        check_resources(where, fm.get("resources", []))

        titles = h2_titles(body)
        if fm.get("type") == "lesson":
            expected = LESSON_SECTIONS + [t for t in LESSON_OPTIONAL if t in titles]
            if titles != expected:
                err(where, f"正文二级标题应为 {expected}，实际是 {titles}")
            check_questions(where, fm.get("questions"))
            if fm.get("minutes", 0) > 60:
                warn(where, "学习卡超过 60 分钟，考虑拆分")
        elif fm.get("type") == "task":
            if titles != TASK_SECTIONS:
                err(where, f"正文二级标题应为 {TASK_SECTIONS}，实际是 {titles}")
            hints = re.findall(r"^### 提示 (\d+)", section_text(body, "提示"), re.M)
            if not hints:
                err(where, "提示部分至少要有一条「### 提示 1」")
            elif hints != [str(i) for i in range(1, len(hints) + 1)]:
                err(where, f"提示编号应从 1 连续递增，实际是 {hints}")
            check_task(where, fm, cid, people)
            if fm.get("minutes", 0) > 60:
                warn(where, "任务超过 60 分钟，考虑拆分")
        if fm.get("retired"):
            warn(where, "这张卡已标记 retired")

    # 前置关系（CONTENT_SPEC v0.6 第 5.1 节）：
    # 隐含前置 = 同一条线里前面最近的一张未退休的卡；requires 指向退休的卡视为已满足；
    # 退休的卡本身不参与前置关系。
    def is_retired(card_id):
        return bool(meta.get(card_id, {}).get("retired"))

    prereq = {}
    for tid, ids in order.items():
        prev_active = None
        for cid in ids:
            if is_retired(cid):
                continue
            p = set()
            if prev_active is not None:
                p.add(prev_active)
            for r in (meta.get(cid, {}).get("requires") or []):
                if r not in card_track:
                    err(f"{tid}/{cid}", f"requires 指向不存在的卡：{r!r}")
                    continue
                if card_track[r] == tid:
                    warn(f"{tid}/{cid}", f"requires 里的 {r} 和本卡同一条线，通常不需要写")
                if is_retired(r):
                    warn(f"{tid}/{cid}", f"requires 里的 {r} 已退休，按规范视为已满足")
                    continue
                p.add(r)
            prereq[cid] = p
            prev_active = cid

    state = {}

    def visit(n, stack):
        state[n] = 1
        for m in prereq.get(n, ()):
            if state.get(m) == 1:
                err("前置关系", f"出现循环：{' → '.join(stack + [n, m])}")
            elif state.get(m) is None:
                visit(m, stack + [n])
        state[n] = 2

    for n in prereq:
        if state.get(n) is None:
            visit(n, [])

    # 没有登记在 course.yaml 里的卡片文件
    for path in CARDS.glob("*/*.md"):
        if path.stem not in card_track:
            warn(str(path.relative_to(ROOT)), "这个文件不在 course.yaml 里，程序不会显示它")
        elif path.parent.name != card_track[path.stem]:
            err(str(path.relative_to(ROOT)), f"文件放错了文件夹，应在 cards/{card_track[path.stem]}/ 下")


if __name__ == "__main__":
    main()
    for line in errors + warnings:
        print(line)
    print(f"\n校验完成：{len(errors)} 个错误，{len(warnings)} 个警告。")
    sys.exit(1 if errors else 0)
