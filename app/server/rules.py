"""Deterministic course rules; all mutations are checked on the server."""
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from .judge import judge
from .progress import FieldConflict


class RuleError(ValueError):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def date(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.astimezone()
    except (TypeError, ValueError, OSError, OverflowError):
        # Local epoch conversion can fail on Windows in positive UTC offsets.
        # The fallback is only a sortable sentinel, not a saved local timestamp.
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def default_record(card):
    result = dict(status='in_progress', started_at=None, updated_at=None, done_at=None,
                  questions={}, checks={}, solution_viewed=False, resources_done=[],
                  ui={'phase': 'learn', 'anchor': '', 'learn_completed': False}, summary='',
                  content_verified_at_completion=None)
    if card.get('writeup'):
        result['writeup'] = {'text': '', 'rubric_checked': []}
    return result


def record_for(card, progress):
    result = default_record(card)
    raw = progress.get('cards', {}).get(card['id'], {})
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(deepcopy(value))
        else:
            result[key] = deepcopy(value)
    return result


def status(card, progress):
    records = progress.get('cards', {})
    if records.get(card['id'], {}).get('status') == 'done':
        return 'done'
    if card.get('retired'):
        return 'retired'
    if any(records.get(cid, {}).get('status') != 'done' for cid in card['prerequisites']):
        return 'locked'
    return 'in_progress' if card['id'] in records else 'available'


def check_correct(record, cid):
    attempts = record.get('checks', {}).get(cid, {}).get('attempts', [])
    return bool(attempts and attempts[-1].get('correct') is True)


def solution_unlocked(card, progress):
    record = record_for(card, progress)
    return card['type'] == 'task' and (record['status'] == 'done' or any(
        sum(attempt.get('correct') is False for attempt in check.get('attempts', [])) >= 3
        for check in record['checks'].values()))


# Learning preference from settings.json (CONTENT_SPEC v0.8): set per request by the hub.
SUMMARY_REQUIRED = ContextVar('summary_required', default=False)


def missing(card, progress, require_summary=None):
    if require_summary is None:
        require_summary = SUMMARY_REQUIRED.get()
    record = record_for(card, progress)
    result = []
    if card['type'] == 'lesson':
        for q in card['questions']:
            if record['questions'].get(q['id'], {}).get('self_mark') != 'correct':
                result.append({'label': '自测题 ' + q['id'] + ' 还没标记答对', 'phase': 'practice', 'anchor': q['id']})
    else:
        for c in card['checks']:
            if not check_correct(record, c['id']):
                result.append({'label': '任务题 ' + c['id'] + ' 还没答对', 'phase': 'practice', 'anchor': c['id']})
        if card.get('writeup'):
            if not record['writeup']['text'].strip():
                result.append({'label': '还没写文字结论', 'phase': 'wrap', 'anchor': 'writeup'})
            for i, text in enumerate(card['writeup']['rubric']):
                if i not in record['writeup']['rubric_checked']:
                    result.append({'label': '文字结论自查：' + text, 'phase': 'wrap', 'anchor': 'rubric-' + str(i)})
    if require_summary and not record['summary'].strip():
        result.append({'label': '还没写一句话总结', 'phase': 'wrap', 'anchor': 'summary'})
    return result


def phase_status(card, progress):
    r = record_for(card, progress)
    return {'learn': r['ui']['learn_completed'], 'practice': not any(x['phase'] == 'practice' for x in missing(card, progress)),
            'wrap': r['status'] == 'done'}


def unlock_time(card, progress):
    values = [progress.get('cards', {}).get(cid, {}).get('done_at') for cid in card['prerequisites']]
    return max((x for x in values if x), key=date, default=None)


def recommendations(content, progress, previous_visit=None, at=None):
    at = date(at or now())
    cards = [content.cards[cid] for cid in content.all_ids if cid in content.cards and not content.cards[cid].get('retired')]
    ongoing = [c for c in cards if status(c, progress) == 'in_progress']
    result = []
    chosen = None
    if ongoing:
        chosen = max(ongoing, key=lambda c: date(record_for(c, progress)['updated_at']))
        result.append((chosen, '上次学到一半'))
    candidates = []
    for order, track in enumerate(content.course.get('tracks', [])):
        track_cards = [content.cards[cid] for cid in track['cards'] if cid in content.cards and not content.cards[cid].get('retired')]
        candidate = next((c for c in track_cards if c != chosen and status(c, progress) in ('available', 'in_progress')), None)
        if candidate is None:
            continue
        completed = [record_for(content.cards[cid], progress)['done_at'] for cid in track['cards'] if cid in content.cards and status(content.cards[cid], progress) == 'done']
        last = max((x for x in completed if x), key=date, default=None)
        days = (at.date() - date(last).date()).days if last else None
        unlocked = unlock_time(candidate, progress)
        if previous_visit and unlocked and date(unlocked) > date(previous_visit):
            prereq = max(candidate['prerequisites'], key=lambda cid: date(progress.get('cards', {}).get(cid, {}).get('done_at')))
            reason = '完成「%s」后解锁' % content.cards.get(prereq, {'title': prereq})['title']
        elif not any(cid in progress.get('cards', {}) for cid in track['cards']):
            reason = '还没开始这条线'
        elif days is not None and days >= 7:
            reason = '这条线上次学习是 %s 天前' % days
        else:
            reason = '这条线的下一节'
        candidates.append((-(days if days is not None else 10**9), order, candidate, reason))
    result += [(c, reason) for _, _, c, reason in sorted(candidates, key=lambda x: (x[0], x[1]))[:3-len(result)]]
    return result


def _text(payload, key, limit=100000):
    value = payload.get(key)
    if not isinstance(value, str) or len(value) > limit:
        raise RuleError('输入文字的格式不正确或过长。')
    return value


def _index(payload, sequence):
    index = payload.get('index')
    if type(index) is not int or index < 0 or index >= len(sequence):
        raise RuleError('没有找到这一项。')
    return index


def _item(sequence, key):
    item = next((x for x in sequence if x['id'] == key), None)
    if item is None:
        raise RuleError('没有找到这道题。')
    return item


# Operations that never overwrite typed text: program state, set-style marks and
# appended attempts. They apply to the latest record without a revision check.
STATE_OPS = {'visit', 'welcome_done', 'seen_card', 'start_card', 'set_ui', 'mark_question',
             'check', 'view_solution', 'complete'}
TEXT_OPS = {'save_answer', 'save_summary', 'save_writeup'}


def needs_revision(payload):
    """Whole-record revision check only where an operation has nothing finer.

    Typed text carries its own `base` (the text this edit started from) and conflicts
    only when that one field changed elsewhere; checkboxes carry the wanted state.
    """
    op = payload.get('op')
    if op in STATE_OPS:
        return False
    if op in TEXT_OPS:
        return 'base' not in payload
    if op in ('toggle_resource', 'toggle_rubric'):
        return 'done' not in payload
    return True


def _guard_text(current, payload):
    if 'base' not in payload:
        return
    base = payload['base']
    if not isinstance(base, str) or len(base) > 200000:
        raise RuleError('文字基线格式不正确。')
    if (current or '') != base and (current or '') != payload.get('text'):
        raise FieldConflict()


def _set_member(items, index, payload):
    wanted = payload['done'] if 'done' in payload else index not in items
    if not isinstance(wanted, bool):
        raise RuleError('勾选状态格式不正确。')
    if wanted and index not in items:
        items.append(index)
    elif not wanted and index in items:
        items.remove(index)


def perform(content, progress, payload, at=None):
    at = at or now()
    op = payload.get('op')
    if any(issue.get('card') is None for issue in getattr(content, 'issues', [])):
        raise RuleError('课程总表暂时无法读取，学习记录保持原样。请修复内容后刷新。', 503)
    if op == 'visit':
        previous = progress.get('last_visit_at')
        old_version = progress.get('seen_content_version')
        current = content.all_ids
        active = [cid for cid in current if not content.cards.get(cid, {}).get('retired')]
        new = [cid for cid in active if cid not in progress.get('known_cards', current)]
        pending = set(progress.get('pending_new_cards', [])) | set(new)
        progress['pending_new_cards'] = [cid for cid in active if cid in pending] if 'known_cards' in progress else []
        progress.update(known_cards=current, seen_content_version=content.course.get('content_version'), last_visit_at=at)
        return {'previous_visit_at': previous, 'previous_content_version': old_version, 'new_cards': new}
    if op == 'welcome_done':
        progress.setdefault('welcome_seen_at', at)
        return {}
    card = content.cards.get(payload.get('card'))
    if not card:
        raise RuleError('没有找到这张卡，内容可能已更新。', 404)
    if op == 'seen_card':
        progress['pending_new_cards'] = [cid for cid in progress.get('pending_new_cards', []) if cid != card['id']]
        return {}
    if card.get('retired'):
        raise RuleError('这张卡已退休，保留的学习记录可以在记录页查看。', 403)
    if status(card, progress) == 'locked':
        raise RuleError('这张卡还没有解锁，请先完成前置卡。', 403)
    accepted = {'start_card', 'set_ui', 'toggle_resource', 'save_answer', 'mark_question', 'check', 'view_solution', 'save_writeup', 'toggle_rubric', 'save_summary', 'complete'}
    if op not in accepted:
        raise RuleError('没有识别这个操作。')
    fresh = card['id'] not in progress['cards']
    r = record_for(card, progress)
    if fresh:
        r.update(started_at=at, updated_at=at)
        progress['events'].append({'at': at, 'type': 'card_started', 'card': card['id']})
    progress['cards'][card['id']] = r
    result = {}
    event = None
    if op == 'set_ui':
        if payload.get('phase') not in ('learn', 'practice', 'wrap'):
            raise RuleError('学习阶段不正确。')
        if 'learn_completed' in payload and payload['learn_completed'] is not True:
            raise RuleError('已读完的学习阶段不能撤销。')
        r['ui'].update(phase=payload['phase'], anchor=_text(payload, 'anchor', 300))
        if payload.get('learn_completed') is True:
            r['ui']['learn_completed'] = True
    elif op == 'toggle_resource':
        _set_member(r['resources_done'], _index(payload, card.get('resources', [])), payload)
    elif op in ('save_answer', 'mark_question'):
        q = _item(card.get('questions', []), payload.get('q'))
        answer = r['questions'].setdefault(q['id'], {'my_answer': '', 'self_mark': None})
        if op == 'save_answer':
            _guard_text(answer.get('my_answer'), payload)
            answer['my_answer'] = _text(payload, 'text')
        else:
            if payload.get('mark') not in ('correct', 'wrong'):
                raise RuleError('自测题标记不正确。')
            answer['self_mark'] = payload['mark']
            event = {'type': 'question_marked', 'q': q['id'], 'mark': payload['mark']}
        answer['at'] = at
    elif op == 'check':
        c = _item(card.get('checks', []), payload.get('c'))
        value = _text(payload, 'value', 10000)
        correct = judge(c, value)
        attempts = r['checks'].setdefault(c['id'], {'attempts': []})['attempts']
        attempts.append({'value': value, 'correct': correct, 'at': at})
        result = {'correct': correct, 'attempts': len(attempts)}
        event = {'type': 'check_attempt', 'c': c['id'], 'correct': correct}
    elif op == 'view_solution':
        if not solution_unlocked(card, progress):
            raise RuleError('完成任务，或同一道题答错 3 次后，才能查看参考解法。', 403)
        r['solution_viewed'] = True
        event = {'type': 'solution_viewed'}
    elif op == 'save_writeup':
        if not card.get('writeup'):
            raise RuleError('这张卡不需要文字结论。')
        _guard_text(r['writeup'].get('text'), payload)
        r['writeup']['text'] = _text(payload, 'text')
        event = {'type': 'writeup_saved'}
    elif op == 'toggle_rubric':
        _set_member(r['writeup']['rubric_checked'], _index(payload, card.get('writeup', {}).get('rubric', [])), payload)
    elif op == 'save_summary':
        _guard_text(r.get('summary'), payload)
        r['summary'] = _text(payload, 'text')
        event = {'type': 'summary_saved'}
    elif op == 'complete':
        needed = missing(card, progress)
        if needed:
            raise RuleError('还不能完成：' + '；'.join(x['label'] for x in needed), 422)
        if r['status'] != 'done':
            r.update(status='done', done_at=at, content_verified_at_completion=card['verified'])
            event = {'type': 'card_done'}
        result = {'completed': card['id']}
    if op != 'start_card' or fresh:
        r['updated_at'] = at
    if event:
        progress['events'].append(dict(event, at=at, card=card['id']))
    return result
