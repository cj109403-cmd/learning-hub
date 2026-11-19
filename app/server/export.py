"""Text copied to AI is assembled from explicit public fields, never raw cards."""
from datetime import datetime
from . import rules
from .progress import _atomic_write, _ensure_directory


def record_text(content, progress, at=None):
    stamp = datetime.fromisoformat(at) if at else datetime.now().astimezone()
    rows = ['# 学习记录摘要（导出于 %s）' % stamp.strftime('%Y-%m-%d %H:%M'), '', '## 各条线进度']
    cards = [content.cards[cid] for cid in content.all_ids if cid in content.cards and not content.cards[cid].get('retired')]
    for track in content.course.get('tracks', []):
        group = [c for c in cards if c['track_id'] == track['id']]
        ongoing = ['%s %s' % (c['code'], c['title']) for c in group if rules.status(c, progress) == 'in_progress']
        rows.append('- %s %s：完成 %s / %s%s' % (track['label'], track['title'], sum(rules.status(c, progress) == 'done' for c in group), len(group), '；进行中：' + '、'.join(ongoing) if ongoing else ''))
    def section(title, values):
        rows.extend(['', '## ' + title])
        rows.extend(['- ' + x for x in values] or ['- 无'])
    historical = [content.cards[cid] for cid in content.all_ids if cid in content.cards]
    completed = sorted((c for c in historical if rules.status(c, progress) == 'done'), key=lambda c: rules.date(rules.record_for(c, progress)['done_at']), reverse=True)[:10]
    def finished(c):
        r = rules.record_for(c, progress)
        line = '%s %s %s' % (rules.date(r['done_at']).strftime('%m-%d'), c['code'], c['title'])
        return line + ('："%s"' % r['summary'] if r['summary'].strip() else '（未写总结）')
    section('最近完成（最多 10 条，新的在前）', [finished(c) for c in completed])
    wrong, attempts, solutions, stale = [], [], [], []
    for c in cards:
        r = rules.record_for(c, progress)
        for q in c.get('questions', []):
            if r['questions'].get(q['id'], {}).get('self_mark') == 'wrong':
                wrong.append('%s %s %s：%s' % (c['code'], c['title'], q['id'], q['prompt'][:40].replace('\n', ' ')))
        for check in c.get('checks', []):
            history = r['checks'].get(check['id'], {}).get('attempts', [])
            if sum(x.get('correct') is False for x in history) >= 3:
                attempts.append('%s %s %s：已尝试 %s 次，%s' % (c['code'], c['title'], check['id'], len(history), '已答对' if rules.check_correct(r, check['id']) else '尚未答对'))
        if c['type'] == 'task' and r['solution_viewed']:
            solutions.append(c['code'] + ' ' + c['title'])
        if rules.status(c, progress) == 'in_progress' and (stamp - rules.date(r['updated_at'])).total_seconds() > 14*86400:
            stale.append(c['code'] + ' ' + c['title'])
    section('自测题里标记"没答对"、还没改过来的', wrong)
    section('任务里答错 3 次以上的题', attempts)
    section('查看过参考解法的任务', solutions)
    section('进行中但超过 14 天没动的卡', stale)
    return '\n'.join(rows) + '\n'


def ai_text(content, progress, payload):
    c = content.cards.get(payload.get('card'))
    if c is None or c.get('retired') or rules.status(c, progress) == 'locked':
        raise rules.RuleError('请先打开已解锁的卡片。', 403)
    r = rules.record_for(c, progress)
    kind = payload.get('kind')
    if kind == 'help':
        problem, code = payload.get('problem', ''), payload.get('code', '')
        if not isinstance(problem, str) or not isinstance(code, str) or len(problem) + len(code) > 100000:
            raise rules.RuleError('输入文字格式不正确或过长。')
        if c['type'] == 'task':
            related = c['sections'].get('任务说明', '')[:600]
            for check in c['checks']:
                attempts = r['checks'].get(check['id'], {}).get('attempts', [])
                related += '\n\n%s：%s\n我最近的回答：%s' % (check['id'], check['prompt'], attempts[-1]['value'] if attempts else '尚未回答')
        else:
            related = '\n'.join('- ' + title for title in c['headings'])
        return '我正在学习一张卡片，卡住了。请像一个耐心的师兄或师姐一样帮我：先给提示，引导我自己想出来，不要直接给出完整答案。\n\n【卡片】%s %s（%s）\n【这张卡要学会的】%s\n【我卡在哪里】%s\n【我的代码或报错】\n%s\n【相关内容】\n%s' % (c['code'], c['title'], c['track_title'], c['subtitle'], problem, code or '无', related)
    if kind == 'question':
        q = rules._item(c.get('questions', []), payload.get('q'))
        answer = r['questions'].get(q['id'], {}).get('my_answer', '')
        return '请帮我批改一道自测题。先判断我的回答对不对、哪里不完整，再用简单的话解释原因。\n\n【题目】%s\n【我的回答】%s\n【参考答案】%s' % (q['prompt'], answer or '我还没写，请先给我一点思路', q['answer'])
    if kind == 'writeup' and c.get('writeup'):
        rubric = '\n'.join('%s. %s' % (i + 1, text) for i, text in enumerate(c['writeup']['rubric']))
        return '请帮我点评一段文字结论。按下面的自查要点逐条判断我做到了没有，再给出具体的修改建议。\n\n【任务】%s\n【要求】%s\n【自查要点】\n%s\n【我写的内容】\n%s' % (c['title'], c['writeup']['prompt'], rubric, r['writeup']['text'])
    raise rules.RuleError('没有识别这个导出类型。')


def save_record(progress_dir, content, progress):
    stamp = datetime.now().astimezone()
    text = record_text(content, progress, stamp.isoformat())
    directory = progress_dir / 'exports'
    _ensure_directory(directory)
    path = directory / ('学习记录-' + stamp.strftime('%Y%m%d-%H%M') + '.md')
    _atomic_write(path, text.encode('utf-8'))
    return {'text': text, 'path': str(path)}
