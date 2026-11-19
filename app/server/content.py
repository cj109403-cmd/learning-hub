"""Read the content contract without executing course code or repairing files."""
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Dict, List
from urllib.parse import urlsplit
from app.vendor_py import yaml
from app.file_safety import assert_no_symlinks, safe_relative

ID = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
KINDS = {'integer', 'number', 'text', 'choice', 'set'}
NORMALIZE = {'trim', 'upper', 'lower', 'no_space'}


@dataclass
class ContentSnapshot:
    course: dict
    cards: Dict[str, dict]
    issues: List[dict]

    @property
    def all_ids(self):
        return [cid for t in self.course.get('tracks', []) for cid in t['cards']]


def plain(value, active=None, depth=0):
    if depth > 80:
        raise ValueError('内容嵌套层级过深')
    active = set() if active is None else active
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        if id(value) in active:
            raise ValueError('内容包含循环引用')
        active.add(id(value))
        try:
            if isinstance(value, dict):
                return {str(k): plain(v, active, depth + 1) for k, v in value.items()}
            return [plain(v, active, depth + 1) for v in value]
        finally:
            active.remove(id(value))
    return value


def valid_url(value):
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme in ('http', 'https') and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def content_path(root, relative):
    """Reject an unsafe reference before touching any referenced file."""
    p = safe_relative(relative)
    if p.parts[0] not in ('content', 'data'):
        raise ValueError('文件路径越界')
    return assert_no_symlinks(root, p.as_posix())


def split_markdown(text):
    if not text.startswith('---\n') or '\n---\n' not in text[4:]:
        raise ValueError('缺少 YAML 头信息')
    front, body = text[4:].split('\n---\n', 1)
    meta = yaml.safe_load(front)
    if not isinstance(meta, dict):
        raise ValueError('YAML 头信息必须是字段表')
    sections, titles, lines, current, fence = {}, [], [], None, None
    for line in body.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            token, rest = marker.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
            lines.append(line)
            continue
        heading = re.match(r'^## (.+?)\s*$', line) if fence is None else None
        if heading:
            if current is not None:
                sections[current] = '\n'.join(lines).strip()
            current = heading.group(1); titles.append(current); lines = []
        else:
            lines.append(line)
    if current is not None:
        sections[current] = '\n'.join(lines).strip()
    return plain(meta), sections, titles


def subheadings(markdown):
    result, fence = [], None
    for line in markdown.splitlines():
        m = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if m:
            token, rest = m.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
            continue
        if fence is None and line.startswith('### '):
            result.append(line[4:].strip())
    return result


def split_hints(markdown):
    chunks, lines, fence = [], [], None
    for line in markdown.splitlines():
        m = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if m:
            token, rest = m.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
        if fence is None and re.match(r'^### 提示\s*\d+', line):
            if lines and '\n'.join(lines).strip():
                chunks.append('\n'.join(lines).strip())
            lines = []
        else:
            lines.append(line)
    if '\n'.join(lines).strip():
        chunks.append('\n'.join(lines).strip())
    return chunks


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_course(c):
    require(isinstance(c, dict) and c.get('spec_version') == 1, '课程格式版本不受支持')
    require(isinstance(c.get('content_version'), str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}\.\d+', c['content_version']), '内容版本格式不正确')
    require(isinstance(c.get('tracks'), list), '课程缺少线路列表')
    require(isinstance(c.get('lab'), dict) and isinstance(c['lab'].get('name'), str) and isinstance(c['lab'].get('fictional'), bool), '课题组信息不完整')
    people = c.get('people')
    require(isinstance(people, list) and people, '缺少人物信息')
    ids = []
    for p in people:
        require(isinstance(p, dict) and all(isinstance(p.get(k), str) and p[k] for k in ('id','name','role','initial')), '人物信息不完整')
        require(ID.fullmatch(p['id']) and len(p['initial']) == 1, '人物 id 或头像首字不正确')
        ids.append(p['id'])
    require(len(set(ids)) == len(ids), '人物 id 重复')
    welcome = c.get('welcome', {})
    require(isinstance(welcome, dict) and welcome.get('from') in ids and isinstance(welcome.get('message'), str), '欢迎消息的人物或正文不正确')
    track_ids, card_ids = [], []
    for t in c['tracks']:
        require(isinstance(t, dict) and isinstance(t.get('id'), str) and ID.fullmatch(t['id']), '线路 id 不正确')
        require(all(isinstance(t.get(k), str) and t[k] for k in ('label','title','summary')) and isinstance(t.get('cards'), list), '线路字段不完整')
        require(all(isinstance(x,str) and ID.fullmatch(x) for x in t['cards']), '卡片 id 不正确')
        track_ids.append(t['id']);card_ids.extend(t['cards'])
    require(len(track_ids)==len(set(track_ids)), '线路 id 重复')
    require(len(card_ids)==len(set(card_ids)), '卡片在课程总表中重复')


def validate_card(c, sections, titles, cid, people, root):
    require(c.get('id') == cid, '卡片 id 与文件名不一致')
    require(c.get('type') in ('lesson','task'), '卡片类型不正确')
    require(all(isinstance(c.get(k), str) and c[k].strip() for k in ('title','subtitle')), '缺少标题或副标题')
    require(type(c.get('minutes')) is int and c['minutes']>0, '时长必须是正整数')
    require(isinstance(c.get('requires'), list) and all(isinstance(x,str) and ID.fullmatch(x) for x in c['requires']), '前置卡格式不正确')
    try: date.fromisoformat(str(c.get('verified', '')))
    except ValueError: raise ValueError('缺少有效的核实日期') from None
    if 'retired' in c: require(type(c['retired']) is bool, 'retired 必须是布尔值')
    expected = ['讲解','怎么学'] if c['type']=='lesson' else ['任务说明','提示','参考解法']
    require(titles == expected or (c['type']=='lesson' and titles==expected+['延伸']), '正文二级标题缺失、重复或顺序不正确')
    require(all(sections.get(s,'').strip() for s in expected), '正文必需章节为空')
    require(isinstance(c.get('resources'),list), '缺少资源列表')
    for r in c['resources']:
        require(isinstance(r,dict) and all(k in r for k in ('kind','title','source','url','embed','lang','minutes','required','focus')), '资源字段不完整')
        require(r['kind'] in ('video','course','practice','reading') and r['embed'] in ('youtube','bilibili','none') and r['lang'] in ('en','zh'), '资源类型不正确')
        require(all(isinstance(r[k],str) for k in ('title','source','url','focus')) and valid_url(r['url']), '资源链接或文字格式不正确')
        require(type(r['required']) is bool and type(r['minutes']) is int and r['minutes']>0, '资源时长或必读标记不正确')
        require(r['embed']=='none' or r['kind']=='video', '非视频资源不能内嵌')
    if c['type']=='lesson':
        qs=c.get('questions');require(isinstance(qs,list) and 3<=len(qs)<=5, '自测题必须为 3–5 道')
        require(all(isinstance(q,dict) and all(isinstance(q.get(k),str) and q[k] for k in ('id','kind','prompt','answer')) and ID.fullmatch(q['id']) and q['kind'] in ('concept','predict','apply') for q in qs), '自测题字段不完整或格式不正确')
        require(len({q['id'] for q in qs})==len(qs), '自测题 id 重复')
        require(any(q['kind'] in ('predict','apply') for q in qs), '缺少预测或应用自测题')
        return
    require(c.get('from') in people, '任务人物不在 course.people 中')
    require(isinstance(c.get('message'),str) and c['message'].strip(), '缺少导师消息')
    require(type(c.get('simulated')) is bool and type(c.get('capstone')) is bool, '模拟数据或毕业任务标记不正确')
    if not c['simulated']:
        require(isinstance(c.get('source'),dict) and all(isinstance(c['source'].get(k), str) and c['source'][k].strip() for k in ('citation','url','license')) and valid_url(c['source']['url']), '真实数据来源不完整')
    data=c.get('data',[]);require(isinstance(data,list), '数据文件列表不正确')
    for d in data:
        require(isinstance(d,dict) and isinstance(d.get('file'),str) and d['file'] and isinstance(d.get('description'),str), '数据文件字段不正确')
        path=content_path(root,'data/tasks/'+cid+'/'+d['file']);require(path.is_file(), '任务数据文件缺失')
    checks=c.get('checks');require(isinstance(checks,list) and checks, '任务至少需要一个 check')
    ids=[]
    for check in checks:
        require(isinstance(check,dict) and isinstance(check.get('id'),str) and ID.fullmatch(check['id']) and isinstance(check.get('prompt'),str) and check['prompt'], 'check 字段不完整')
        ids.append(check['id']);kind=check.get('kind');require(kind in KINDS and 'expected' in check, 'check 类型或判定字段缺失')
        require(isinstance(check.get('normalize',[]),list) and all(x in NORMALIZE for x in check.get('normalize',[])), '规范化选项不正确')
        if kind=='integer': require(type(check['expected']) is int, '整数判定字段不正确')
        if kind=='number':
            import math
            require(type(check['expected']) in (int,float) and math.isfinite(check['expected']) and type(check.get('tolerance')) in (int,float) and math.isfinite(check['tolerance']) and check['tolerance']>=0, '数值判定字段不正确')
        if kind=='text': require(isinstance(check['expected'],str), '文字判定字段不正确')
        if kind=='set': require(isinstance(check['expected'],list), '集合判定字段不正确')
        if kind=='choice':
            options=check.get('options');require(isinstance(options,list) and options and all(isinstance(x,dict) and isinstance(x.get('id'),str) and isinstance(x.get('text'),str) for x in options), '选项格式不正确')
            require(check['expected'] in [x['id'] for x in options] and len({x['id'] for x in options})==len(options), '选择题判定字段不正确')
    require(len(ids)==len(set(ids)), 'check id 重复')
    if 'writeup' in c:
        w=c['writeup'];require(isinstance(w,dict) and isinstance(w.get('prompt'),str) and isinstance(w.get('rubric'),list) and all(isinstance(x,str) and x for x in w['rubric']), '文字结论要求不完整')
    if 'debrief' in c: require(isinstance(c['debrief'],str), '导师回复格式不正确')
    answers_path=content_path(root,'data/generators/'+cid+'.answers.json')
    try: answers=json.loads(answers_path.read_text(encoding='utf-8'))['checks']
    except (OSError,ValueError,KeyError,TypeError): raise ValueError('任务标准答案文件缺失或格式不正确') from None
    for check in checks:
        answer=answers.get(check['id'])
        # Generator files hold the expected value directly under each check id.
        if isinstance(answer,dict) and 'expected' in answer: answer=answer['expected']
        a,b=check['expected'],answer
        if check['kind']=='set': equal=isinstance(b,list) and sorted(map(str,a))==sorted(map(str,b))
        elif check['kind']=='number':
            try: equal=abs(float(a)-float(b))<1e-9
            except (TypeError,ValueError): equal=False
        else: equal=a==b
        require(equal, '任务判定与生成器答案不一致')


def load_content(root):
    root=Path(root);issues=[]
    fallback={'spec_version':1,'content_version':'','lab':{'name':'学习台','fictional':True},'people':[],'welcome':{},'tracks':[]}
    try:
        course=plain(yaml.safe_load(content_path(root,'content/course.yaml').read_text(encoding='utf-8')))
        validate_course(course)
    except (ValueError,OSError,yaml.YAMLError,TypeError,RecursionError):
        return ContentSnapshot(fallback,{},[{'card':None,'message':'课程总表无法加载，请检查格式和文件。'}])
    cards={};people={p['id'] for p in course['people']}
    all_ids={cid for t in course['tracks'] for cid in t['cards']}
    for track in course['tracks']:
        for index,cid in enumerate(track['cards']):
            try:
                text=content_path(root,'content/cards/'+track['id']+'/'+cid+'.md').read_text(encoding='utf-8')
                c,sections,titles=split_markdown(text);validate_card(c,sections,titles,cid,people,root)
                require(all(x in all_ids for x in c['requires']), '前置关系指向不存在的卡片')
                c.update(sections=sections,hints=split_hints(sections.get('提示','')),headings=subheadings(sections.get('讲解','')),code=track['label']+str(index+1),number=index+1,track_id=track['id'],track_label=track['label'],track_title=track['title'])
                cards[cid]=c
            except yaml.YAMLError:
                issues.append({'card':cid,'message':'YAML 头信息格式不正确'})
            except OSError:
                issues.append({'card':cid,'message':'卡片或关联文件无法读取'})
            except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as e:
                issues.append({'card':cid,'message':str(e) if isinstance(e,ValueError) else '卡片字段结构不正确'})
    # Retirement is known only after all cards load. An invalid card is never
    # silently skipped as a prerequisite; only an explicit valid retired flag is.
    for track in course['tracks']:
        previous = None
        for cid in track['cards']:
            card = cards.get(cid)
            if card is not None:
                extra = [dep for dep in card['requires'] if not cards.get(dep, {}).get('retired')]
                card['prerequisites'] = list(dict.fromkeys(([previous] if previous else []) + extra))
            if not (card and card.get('retired')):
                previous = cid
    visiting,visited,cycles=set(),set(),set()
    def visit(cid, stack):
        if cid in visiting:
            cycles.update(stack[stack.index(cid):]);return
        if cid in visited or cid not in cards:return
        visiting.add(cid)
        for dep in cards[cid]['prerequisites']:visit(dep,stack+[cid])
        visiting.remove(cid);visited.add(cid)
    for cid in list(cards):visit(cid,[])
    for cid in sorted(cycles):
        cards.pop(cid,None);issues.append({'card':cid,'message':'前置关系存在循环'})
    return ContentSnapshot(course,cards,issues)
