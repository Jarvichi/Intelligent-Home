import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import halloween_art


class HalloweenArtTests(unittest.TestCase):
    def test_header_has_original_web_spider_bats_moon_and_fangs(self):
        root = ET.fromstring('<svg>' + halloween_art.header_decor() + '</svg>')
        group = root.find(".//*[@id='k_hw_h']")
        self.assertIsNotNone(group.find(".//path[@class='hwl']"))
        self.assertEqual(len(group.findall(".//path[@class='hwf']")), 3)
        self.assertIsNotNone(group.find(".//circle[@cx='572'][@cy='150']"))
        self.assertIsNotNone(group.find(".//path[@class='hwd']"))
        self.assertIn('stroke:#fff', group.find('style').text)
        self.assertNotIn('hide', group.attrib.get('class', ''))

    def test_pumpkin_has_original_placement_and_grayscale_style(self):
        root = ET.fromstring('<svg>' + halloween_art.body_decor() + '</svg>')
        self.assertIsNotNone(root.find(".//*[@transform='translate(944,984)']/ellipse"))
        self.assertIn('fill:#d4d4d4', root.find('.//style').text)


if __name__ == '__main__':
    unittest.main()
