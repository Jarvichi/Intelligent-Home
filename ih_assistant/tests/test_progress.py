import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant, load_options
from discord_io import DiscordError


class ReplyProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = PersonaStore(self.tmp.name, 'sample', lambda: self.now[0])
        self.store.ensure_reply_progress()

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_direct_messages_enter_stream_and_snapshot_has_no_content(self):
        first = self.store.append_event({'type': 'message', 'msg_id': '501', 'text': 'private text'})
        self.store.append_event({'type': 'checkin_due', 'slot': '09:00'})
        second = self.store.append_event({'type': 'message', 'msg_id': '502', 'text': 'more private text'})
        snap = self.store.reply_progress_snapshot()
        self.assertEqual((first['progress_seq'], second['progress_seq']), (1, 2))
        self.assertEqual((snap['produced_seq'], snap['acked_seq']), (2, 0))
        self.assertTrue(snap['consistent'])
        self.assertNotIn('private text', repr(snap))
        self.assertNotIn('msg_id', snap)

    def test_out_of_order_receipt_does_not_jump_contiguous_watermark(self):
        self.store.append_event({'type': 'message', 'msg_id': '501'})
        self.store.append_event({'type': 'message', 'msg_id': '502'})
        self.assertTrue(self.store.complete_message(msg_id='502'))
        self.assertEqual(self.store.reply_progress_snapshot()['acked_seq'], 0)
        self.assertTrue(self.store.complete_message(msg_id='501'))
        self.assertEqual(self.store.reply_progress_snapshot()['acked_seq'], 2)

    def test_receipt_by_event_id_is_idempotent_and_persists(self):
        event = self.store.append_event({'type': 'message', 'msg_id': '501'})
        self.assertTrue(self.store.complete_message(event_id=event['id']))
        self.assertTrue(self.store.complete_message(event_id=event['id']))
        reopened = PersonaStore(self.tmp.name, 'sample', lambda: self.now[0])
        self.assertEqual(reopened.reply_progress_snapshot()['acked_seq'], 1)

    def test_unmatched_or_non_message_event_cannot_ack(self):
        event = self.store.append_event({'type': 'checkin_due'})
        self.assertFalse(self.store.complete_message(event_id=event['id']))
        self.assertFalse(self.store.complete_message(msg_id='missing'))

    def test_missing_unacknowledged_inbox_row_is_inconsistent(self):
        self.store.append_event({'type': 'message', 'msg_id': '501'})
        self.store._write_atomic('inbox', '')
        self.assertFalse(self.store.reply_progress_snapshot()['consistent'])

    def test_only_confirmed_reply_records_completion(self):
        options = {'mode': 'live', 'api_token': 'unit-test', 'personas': [
            {'key': 'sample', 'user_id': '123', 'bot_token': 'fake', 'live': True}
        ]}
        app = Assistant(options, data_dir=self.tmp.name, clock=lambda: self.now[0])
        event = app.stores['sample'].append_event({'type': 'message', 'msg_id': '501'})

        class DiscordOK:
            def send(self, channel, text, reply_to):
                return True

        app.discord['sample'] = DiscordOK()
        app.stores['sample'].update_state(lambda s: s.__setitem__('dm_channel', '900'))
        code, result = app.reply('sample', {'text': 'reply', 'reply_to': '501'})
        self.assertEqual(code, 200)
        self.assertTrue(result['completion_recorded'])
        self.assertEqual(app.stores['sample'].reply_progress_snapshot()['acked_seq'], 1)

        app.stores['sample'].append_event({'type': 'message', 'msg_id': '502'})

        class DiscordFail:
            def send(self, channel, text, reply_to):
                raise DiscordError('unavailable')

        app.discord['sample'] = DiscordFail()
        code, _ = app.reply('sample', {'text': 'not delivered', 'reply_to': '502'})
        self.assertEqual(code, 502)
        self.assertEqual(app.stores['sample'].reply_progress_snapshot()['acked_seq'], 1)


if __name__ == '__main__':
    unittest.main()
