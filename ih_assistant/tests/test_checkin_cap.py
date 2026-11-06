import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant


class CapTests(unittest.TestCase):
    def test_replies_do_not_use_up_the_checkin_allowance(self):
        a = Assistant.__new__(Assistant)
        tmp = tempfile.TemporaryDirectory()
        a.clock = lambda: 1793955600.0; a.tz = None
        a.stores = {'aa': PersonaStore(tmp.name, 'aa', a.clock)}
        for _ in range(12):
            a._count_out('aa', 'reply')
        self.assertEqual((a.out_today('aa'), a.checkins_today('aa')), (12, 0))
        a._count_out('aa', 'checkin')
        self.assertEqual((a.out_today('aa'), a.checkins_today('aa')), (13, 1))


if __name__ == '__main__':
    unittest.main()
