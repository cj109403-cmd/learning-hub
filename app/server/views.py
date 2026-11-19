"""Public payloads. Internal check answers and closed solutions never leave here."""
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
from .content import plain
from . import rules

# Display settings that belong to one particular course rather than to the engine or the
# course format (today: whose name task hints carry). The file is optional; the open-source
# export leaves it out.
EXTRAS = Path(__file__).resolve().parents[1] / 'course_extras.json'

PUBLIC_FIELDS = ('id', 'type', 'title', 'subtitle', 'minutes', 'tags', 'verified', 'updated',
                 'retired', 'requires', 'code', 'number', 'track_id', 'track_label', 'track_title',
                 'resources', 'questions', 'from', 'message', 'simulated', 'source', 'capstone',
                 'debrief', 'data', 'writeup', 'headings', 'takeaway')


@lru_cache(maxsize=4)
def _read_extras(stamp):
    try:
        data = json.loads(EXTRAS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def course_extras():
    try:
        return _read_extras(EXTRAS.stat().st_mtime_ns)
    except OSError:
        return {}


def display_tracks(content):
    """The course's tracks without retired cards. A track listed with no cards yet is shown
    as "即将推出" on the roadmap and today pages."""
    result = deepcopy(content.course.get('tracks', []))
    for track in result:
        track['cards'] = [cid for cid in track['cards'] if not content.cards.get(cid, {}).get('retired')]
    return result


def hints_author(content):
    """Whose name task hints carry when a card does not say: course.yaml, then course extras."""
    people = {p['id'] for p in content.course.get('people', [])}
    for value in (content.course.get('hints_from'), course_extras().get('hints_from')):
        if isinstance(value, str) and value in people:
            return value
    return None


def public_course(content):
    result = {key: plain(deepcopy(content.course[key])) for key in ('spec_version', 'content_version', 'lab', 'people', 'welcome', 'tracks') if key in content.course}
    result['tracks'] = display_tracks(content)
    author = hints_author(content)
    if author:
        result['hints_from'] = author
    return result


def public_card(content, card, progress):
    result = {key: plain(deepcopy(card[key])) for key in PUBLIC_FIELDS if key in card}
    result['checks'] = [{key: deepcopy(c[key]) for key in ('id', 'prompt', 'kind', 'options') if key in c} for c in card.get('checks', [])]
    result['sections'] = {key: value for key, value in card['sections'].items() if key != '参考解法'}
    result['status'] = rules.status(card, progress)
    result['read_only'] = bool(card.get('retired'))
    result['unlocked'] = result['status'] != 'locked' and not result['read_only']
    result['record'] = rules.record_for(card, progress)
    result['locked_reasons'] = [{'id': cid, 'code': content.cards.get(cid, {}).get('code', ''),
                                 'title': content.cards.get(cid, {}).get('title', cid)}
                                for cid in card['prerequisites'] if progress.get('cards', {}).get(cid, {}).get('status') != 'done']
    result['phase_status'] = rules.phase_status(card, progress)
    result['missing'] = rules.missing(card, progress)
    result['solution_unlocked'] = result['unlocked'] and rules.solution_unlocked(card, progress)
    if result['solution_unlocked'] and result['record']['solution_viewed']:
        result['sections']['参考解法'] = card['sections'].get('参考解法', '')
    result['hints'] = card.get('hints', [])
    result['new'] = card['id'] in progress.get('pending_new_cards', [])
    result['content_changed'] = bool(result['record']['content_verified_at_completion'] and result['record']['content_verified_at_completion'] != card['verified'])
    return result


def card_list(content, progress, include_history=False):
    return [public_card(content, content.cards[cid], progress) for cid in content.all_ids
            if cid in content.cards and (not content.cards[cid].get('retired') or
            include_history and progress.get('cards', {}).get(cid, {}).get('status') == 'done')]


def tracks(content, progress):
    result = []
    for track in display_tracks(content):
        cards = [content.cards[cid] for cid in track['cards'] if cid in content.cards and not content.cards[cid].get('retired')]
        result.append(dict(track, total=len(cards), done=sum(rules.status(c, progress) == 'done' for c in cards)))
    return result


def recommended(content, progress, visit):
    return [dict(public_card(content, card, progress), reason=reason) for card, reason in rules.recommendations(content, progress, visit.get('previous_visit_at'))]


def lab(content, progress):
    result = []
    for person in content.course.get('people', []):
        messages = []
        unread = False
        for card in content.cards.values():
            if card.get('retired') or card['type'] != 'task' or card['from'] != person['id'] or rules.status(card, progress) == 'locked':
                continue
            public = public_card(content, card, progress)
            r = public['record']
            messages.append({'kind': 'task', 'card': public, 'text': card['message'], 'at': rules.unlock_time(card, progress)})
            unread = unread or public['status'] == 'available'
            if r['status'] == 'done' and r['summary'].strip():
                # No empty bubble when the optional summary was left blank (CODEX_BRIEF 18.7 #5).
                messages.append({'kind': 'summary', 'card': public, 'text': r['summary'], 'at': r['done_at']})
                if card.get('debrief'):
                    messages.append({'kind': 'debrief', 'card': public, 'text': card['debrief'], 'at': r['done_at']})
        # Stable ordering keeps summary immediately before debrief on equal dates.
        messages.sort(key=lambda x: rules.date(x['at']))
        result.append(dict(person, messages=messages, unread=unread))
    return {'people': result}


def today(content, progress, visit):
    recs = recommended(content, progress, visit)
    cards = card_list(content, progress)
    done = sorted((c for c in cards if c['status'] == 'done'), key=lambda c: rules.date(c['record']['done_at']), reverse=True)
    ongoing = sorted((c for c in cards if c['status'] == 'in_progress'), key=lambda c: rules.date(c['record']['updated_at']), reverse=True)
    newly_unlocked = [c for c in content.cards.values() if c['type'] == 'task' and not c.get('retired') and rules.status(c, progress) == 'available' and visit.get('previous_visit_at') and rules.unlock_time(c, progress) and rules.date(rules.unlock_time(c, progress)) > rules.date(visit['previous_visit_at'])]
    if ongoing:
        c = ongoing[0]
        count = sum(x['phase'] == 'practice' for x in c['missing'])
        rest = ('自测还差 %s 题' if c['type'] == 'lesson' else '任务还差 %s 题') % count if count else ('还差文字结论和自查' if any(x['anchor'].startswith(('writeup', 'rubric')) for x in c['missing']) else ('只差写总结' if c['missing'] else '可以完成这一节了'))
        sentence = '上次你学到「%s」，%s。' % (c['title'], rest)
    elif newly_unlocked:
        c = max(newly_unlocked, key=lambda c: rules.date(rules.unlock_time(c, progress)))
        person = next(p for p in content.course['people'] if p['id'] == c['from'])
        sentence = person['name'] + '给你发了一个新任务。'
    elif visit.get('previous_content_version') != content.course.get('content_version') and visit.get('new_cards'):
        sentence = '新加了 %s 张卡。' % len(visit['new_cards'])
    elif done and recs:
        sentence = '上次完成了「%s」。下一张是「%s」。' % (done[0]['title'], recs[0]['title'])
    elif cards and len(done) == len(cards):
        sentence = '这一阶段全部完成了。'
    else:
        sentence = '从第一张卡开始吧。'
    messages = []
    for person in lab(content, progress)['people']:
        for message in person['messages']:
            if message['kind'] != 'summary' and (not recs or message['card']['id'] != recs[0]['id']):
                messages.append(dict(message, person={k: person[k] for k in ('id', 'name', 'role', 'initial')}))
    latest = max(messages, key=lambda x: rules.date(x['at']), default=None)
    recent = sorted((c for c in cards if c['record']['summary'].strip()), key=lambda c: rules.date(c['record']['updated_at']), reverse=True)[:4]
    return {'sentence': sentence, 'main': recs[0] if recs else None, 'others': recs[1:], 'recent': recent, 'latest_message': latest, 'tracks': tracks(content, progress)}


def route(content, progress, visit):
    recs = recommended(content, progress, visit)
    result = []
    for track in tracks(content, progress):
        dependencies = list(dict.fromkeys(dep for cid in track['cards'] if cid in content.cards for dep in content.cards[cid]['prerequisites'] if dep in content.cards and content.cards[dep]['track_id'] != track['id']))
        note = '跨线前置：' + '、'.join(content.cards[cid]['code'] + ' ' + content.cards[cid]['title'] for cid in dependencies) if dependencies else ''
        result.append(dict(track, dependency_note=note, cards=[public_card(content, content.cards[cid], progress) for cid in track['cards'] if cid in content.cards and not content.cards[cid].get('retired')]))
    return {'tracks': result, 'current': recs[0]['id'] if recs else None}


def journal(content, progress, view='timeline', track=''):
    notes = []
    for card in card_list(content, progress, include_history=True):
        done = card['record']['status'] == 'done'
        if (not track or card['track_id'] == track) and (card['record']['summary'].strip() or done):
            at = card['record']['done_at'] or card['record']['updated_at']
            dt = rules.date(at)
            today_date = datetime.now().astimezone().date()
            week_age = (today_date.toordinal() - today_date.weekday() - dt.date().toordinal() + dt.weekday()) // 7
            week = '本周' if week_age == 0 else ('上周' if week_age == 1 else '%s 年 %s 月第 %s 周' % (dt.year, dt.month, (dt.day - 1) // 7 + 1))
            notes.append({'card': card, 'text': card['record']['summary'], 'at': at, 'week': week})
    return {'view': view, 'tracks': tracks(content, progress), 'entries': sorted(notes, key=lambda x: rules.date(x['at']), reverse=True),
            'notes': notes, 'copy_text': '\n\n'.join('%s %s\n%s' % (n['card']['code'], n['card']['title'], n['text']) for n in notes if n['text'].strip())}


def search_index(content, progress):
    return {'entries': [{k: card.get(k) for k in ('id', 'code', 'title', 'subtitle', 'headings', 'track_id', 'status', 'updated')} for card in card_list(content, progress)]}


def completion(content, progress, cid, visit):
    card = content.cards[cid]
    recs = recommended(content, progress, visit)
    track = next(t for t in content.course['tracks'] if t['id'] == card['track_id'])
    active = [item for item in track['cards'] if item in content.cards and not content.cards[item].get('retired')]
    return {'card': public_card(content, card, progress), 'next': recs[0] if recs else None,
            'position': {'index': active.index(cid) + 1 if cid in active else 0, 'total': len(active), 'track_id': track['id']}, 'debrief': card.get('debrief')}
