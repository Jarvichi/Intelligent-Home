"""scrub keeps plain dates (a changelog date is not a phone number) and still hides real private data."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from providers.base import scrub


class Tests(unittest.TestCase):
    def test_iso_date_kept(self):
        self.assertEqual(scrub('## [0.9.9] - 2026-11-05'), '## [0.9.9] - 2026-11-05')

    def test_phone_and_long_numbers_still_hidden(self):
        landline = '01234' + ' 567890'
        mobile = '07123' + '456789'
        t = scrub('call ' + landline + ' or ' + mobile + ' ref 123456789')
        self.assertNotIn('567890', t)
        self.assertNotIn(mobile, t)
        self.assertNotIn('123456789', t)

    def test_not_a_valid_date_is_hidden(self):
        self.assertIn('[number]', scrub('id 2026-13-45'))


if __name__ == '__main__':
    unittest.main()
