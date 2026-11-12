"""channels_list parsing: comma-separated names, back-compat with channel_keys. No network."""
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault('IH_TEST', '1')
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main


def load(opts):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'options.json')
        with open(p, 'w') as fh:
            json.dump(opts, fh)
        return main.load_options(p)


class ChannelList(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(main.parse_channel_list('general, test-room ,Other'), ['general', 'test-room', 'other'])

    def test_hash_semicolon_newline_dupes(self):
        self.assertEqual(main.parse_channel_list('#general;test-room\ngeneral'), ['general', 'test-room'])

    def test_list_and_junk(self):
        self.assertEqual(main.parse_channel_list(['general', 5, 'bad key!']), ['general'])
        self.assertEqual(main.parse_channel_list(None), [])
        self.assertEqual(main.parse_channel_list(',, ,'), [])

    def test_cap(self):
        raw = ','.join('c%d' % i for i in range(main.MAX_LIST_ITEMS + 5))
        self.assertEqual(len(main.parse_channel_list(raw)), main.MAX_LIST_ITEMS)

    def test_single_channel_keys_still_work(self):
        self.assertEqual(load({'channel_keys': ['general']})['channel_keys'], ['general'])

    def test_merge_keys_and_list(self):
        self.assertEqual(load({'channel_keys': ['general'], 'channels_list': 'test-room,general'})['channel_keys'], ['general', 'test-room'])


if __name__ == '__main__':
    unittest.main()
