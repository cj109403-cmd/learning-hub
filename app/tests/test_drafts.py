from app.tests.helpers import symlink_or_skip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.server.drafts import DraftError, DraftStore
from app.server.progress import StorageError


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DraftStore(Path(self.temp.name).resolve() / 'progress')

    def put(self, **changes):
        value = dict(client_id='client-one', card='py-first-code', field='summary',
                     sequence=1, text='没有确认的想法', baseline='')
        value.update(changes)
        return self.store.put(value)

    def test_unconfirmed_survives_reopen_without_expiration(self):
        row = self.put()
        with patch('app.server.drafts.local_now', return_value='2099-01-01T00:00:00+08:00'):
            reopened = DraftStore(self.store.directory.parent)
            self.assertEqual(reopened.list()['drafts'], [row])

    def archived(self):
        folder = self.store.directory / 'archive'
        return [json.loads(p.read_text(encoding='utf-8'))['draft'] for p in sorted(folder.glob('*.json'))] if folder.exists() else []

    def test_no_count_pruning(self):
        for index in range(120):
            self.put(field='answer:q%d' % index)
        self.assertEqual(len(self.store.list()['drafts']), 120)
        for index in range(120):
            self.put(client_id='client-%04d' % index, card='py-other', text='第 %d 个窗口' % index)
        live = [d for d in self.store.list()['drafts'] if d['card'] == 'py-other']
        self.assertEqual([d['text'] for d in live], ['第 119 个窗口'])
        self.assertEqual(len(self.archived()), 119)

    def test_newer_window_supersedes_same_field_and_old_version_is_archived(self):
        a = self.put()
        b = self.put(client_id='client-two', text='另一个窗口')
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(self.store.list()['drafts'], [b])
        self.assertEqual([d['text'] for d in self.archived()], ['没有确认的想法'])
        # A late retry of the superseded version cannot bring it back.
        with self.assertRaises(DraftError):
            self.put(text='别的旧文字')
        self.assertEqual(self.store.list()['drafts'], [b])
        # The same window can still keep typing with a newer sequence.
        c = self.put(sequence=2, text='旧窗口继续写的新版本')
        self.assertEqual(self.store.list()['drafts'], [c])

    def test_draft_identical_to_record_is_not_kept(self):
        progress = {'cards': {'py-first-code': {'summary': '已经保存的话'}}}
        row = self.store.put(dict(client_id='client-one', card='py-first-code', field='summary', sequence=1, text='已经保存的话'), progress)
        self.assertTrue(row['resolved'])
        self.assertEqual(self.store.list()['drafts'], [])

    def test_startup_consolidation_keeps_newest_and_archives_the_rest(self):
        with patch('app.server.drafts.local_now', side_effect=['2026-10-09T10:00:00+08:00', '2026-10-09T10:05:00+08:00', '2026-10-09T10:09:00+08:00', '2026-10-09T10:10:00+08:00']):
            for index, text in enumerate(['第一版', '第二版', '已经保存的话']):
                path = self.store._path(self.store._identity(dict(client_id='client-%d' % index, card='py-first-code', field='summary' if index < 2 else 'writeup')))
                entry = dict(client_id='client-%d' % index, card='py-first-code', field='summary' if index < 2 else 'writeup', sequence=1, text=text, id=path.stem, at=['2026-10-09T10:00:00+08:00', '2026-10-09T10:05:00+08:00', '2026-10-09T10:09:00+08:00'][index], baseline=None)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'version': 1, 'sequence': 1, 'draft': entry}, ensure_ascii=False), encoding='utf-8')
            progress = {'cards': {'py-first-code': {'writeup': {'text': '已经保存的话'}}}}
            result = self.store.consolidate(progress)
        self.assertEqual(result, {'resolved': 1, 'archived': 1, 'state': 0})
        self.assertEqual([d['text'] for d in self.store.list()['drafts']], ['第二版'])
        self.assertEqual([d['text'] for d in self.archived()], ['第一版'])

    def test_older_sequence_cannot_overwrite(self):
        self.put(sequence=2, text='新文字')
        with self.assertRaises(DraftError):
            self.put(sequence=1)
        self.assertEqual(self.store.list()['drafts'][0]['text'], '新文字')

    def test_same_sequence_same_text_idempotent(self):
        a = self.put()
        self.assertEqual(self.put(), a)

    def test_same_sequence_changed_text_rejected(self):
        self.put()
        with self.assertRaises(DraftError):
            self.put(text='另外的内容')

    def test_stale_discard_keeps_newer_input(self):
        old = self.put()
        new = self.put(sequence=2)
        self.assertFalse(self.store.discard(old))
        self.assertEqual(self.store.list()['drafts'], [new])

    def test_discard_watermark_rejects_late_put(self):
        row = self.put()
        self.assertTrue(self.store.discard(row))
        self.assertTrue(self.put()['resolved'])
        with self.assertRaises(DraftError):
            self.put(text='同一序号的不同文字')
        self.assertEqual(self.store.list()['drafts'], [])

    def test_confirm_requires_actual_matching_record(self):
        row = self.put()
        self.assertFalse(self.store.confirm(row, {'cards': {}}))
        self.assertFalse(self.store.confirm(row, {'cards': {'py-first-code': {'summary': '别的文字'}}}))
        self.assertEqual(len(self.store.list()['drafts']), 1)
        self.assertTrue(self.store.confirm(row, {'cards': {'py-first-code': {'summary': row['text']}}}))
        self.assertEqual(self.store.list()['drafts'], [])

    def test_stale_confirmation_keeps_newer_input(self):
        row = self.put()
        self.put(sequence=2, text='还在输入')
        self.assertFalse(self.store.confirm(row, {'cards': {'py-first-code': {'summary': row['text']}}}))
        self.assertEqual(self.store.list()['drafts'][0]['text'], '还在输入')

    def test_confirm_answer_writeup_and_check(self):
        cases = [('answer:q1', {'questions': {'q1': {'my_answer': '答复'}}}),
                 ('writeup', {'writeup': {'text': '答复'}}),
                 ('check:c1', {'checks': {'c1': {'attempts': [{'value': '答复'}]}}})]
        for field, record in cases:
            row = self.put(field=field, text='答复')
            self.assertTrue(self.store.confirm(row, {'cards': {'py-first-code': record}}))

    def test_check_only_latest_attempt_can_confirm(self):
        row = self.put(field='check:c1', text='旧答案')
        record = {'checks': {'c1': {'attempts': [{'value': '旧答案'}, {'value': '新答案'}]}}}
        self.assertFalse(self.store.confirm(row, {'cards': {'py-first-code': record}}))

    def write_live(self, client, field, text, card='py-first-code'):
        """A draft file as an older app version left it on disk."""
        identifier = self.store._identity(dict(client_id=client, card=card, field=field))
        path = self.store._path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = dict(client_id=client, card=card, field=field, sequence=3, text=text, id=identifier, at='2026-10-14T15:33:23+08:00', baseline=None)
        path.write_text(json.dumps({'version': 1, 'sequence': 3, 'draft': entry}, ensure_ascii=False), encoding='utf-8')
        return path

    def test_reading_position_is_never_kept_as_a_draft(self):
        position = json.dumps({'phase': 'learn', 'anchor': 'section-0', 'learn_completed': False})
        row = self.put(field='position', text=position)
        self.assertTrue(row['resolved'])
        self.assertEqual(self.store.list()['drafts'], [])
        # A position left by an older version is cleared when the same window writes again...
        self.write_live('client-old', 'position', position)
        self.put(client_id='client-old', field='position', sequence=4, text=position)
        self.assertEqual(self.store.list()['drafts'], [])

    def test_startup_clears_old_position_drafts_but_keeps_typed_text(self):
        position = json.dumps({'phase': 'learn', 'anchor': 'section-0', 'learn_completed': False})
        self.write_live('client-a', 'position', position)
        self.write_live('client-b', 'summary', '没保存的一句话')
        result = self.store.consolidate({'cards': {}})
        self.assertEqual(result['state'], 1)
        self.assertEqual([(d['field'], d['text']) for d in self.store.list()['drafts']], [('summary', '没保存的一句话')])

    def test_settings_requires_server_value(self):
        row = self.put(card='@settings', field='settings', text='{"theme":"paper"}')
        self.assertFalse(self.store.confirm(row, {}, {'theme': 'clear'}))
        self.assertTrue(self.store.confirm(row, {}, {'theme': 'paper', 'mode': 'auto'}))

    def test_atomic_failure_preserves_previous(self):
        old = self.put()
        with patch('app.server.drafts._atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.put(sequence=2, text='新文字')
        self.assertEqual(self.store.list()['drafts'], [old])

    def test_idempotent_retry_must_confirm_durability_again(self):
        self.put()
        with patch('app.server.drafts._atomic_write', side_effect=OSError('fsync failed')):
            with self.assertRaises(OSError):
                self.put()
        self.assertEqual(len(self.store.list()['drafts']), 1)

    def test_corruption_is_preserved_and_visible(self):
        row = self.put()
        path = self.store.directory / (row['id'] + '.json')
        path.write_bytes(b'{broken')
        result = self.store.list()
        self.assertTrue(result['notices'])
        self.assertEqual(path.read_bytes(), b'{broken')
        with self.assertRaises(StorageError):
            self.put(sequence=2)

    def test_symlink_leaf_rejected(self):
        target = Path(self.temp.name) / 'target'
        target.write_text('secret', encoding='utf-8')
        row = self.put()
        path = self.store.directory / (row['id'] + '.json')
        path.unlink();symlink_or_skip(self, path, target)
        self.assertTrue(self.store.list()['notices'])
        self.assertEqual(target.read_text(encoding='utf-8'), 'secret')

    def test_invalid_paths_fields_sequences_and_size(self):
        for payload in [{'card': '../private'}, {'field': 'password'}, {'client_id': '../bad-id'}, {'sequence': True}, {'sequence': 0}, {'text': '字' * 200000}, {'card': '@settings'}]:
            with self.assertRaises(DraftError):
                self.put(**payload)
        with self.assertRaises(DraftError):
            self.store.discard({'id': '../../outside', 'sequence': 1})
