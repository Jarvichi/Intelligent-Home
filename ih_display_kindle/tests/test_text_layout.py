import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import kindle_render

class TextLayoutTests(unittest.TestCase):
    def root(self,data):
        return ET.fromstring(kindle_render.svg(data,{'configured':True,'complete':True,'alerts':[]},now=0))

    def test_door_details_and_media_are_not_cut_at_arbitrary_character_counts(self):
        door='The very long front entrance door name'
        title='A long example song title with substantially more than forty two characters'
        root=self.root({'panels':[{'label':'Doors unlocked','urgent':True,'detail':door},{'kind':'media','active':True,'detail':title}]})
        nodes=root.findall('.//{*}text')
        self.assertIn(door,[n.text for n in nodes])
        self.assertIn(title,[n.text for n in nodes])
        door_node=next(n for n in nodes if n.text==door)
        self.assertLess(int(door_node.get('font-size')),18)

    def test_task_title_and_detail_keep_separate_bounded_spaces(self):
        title='An especially long task title that previously overlapped the detail'
        detail='A detailed reminder with a lengthy name'
        tasks=[{'title':title,'detail':detail},{'title':'Second'},{'title':'Third'}]
        root=self.root({'tasks':tasks})
        nodes=root.findall('.//{*}text')
        title_node=next(n for n in nodes if n.text==title)
        detail_node=next(n for n in nodes if n.text==detail)
        self.assertLessEqual(kindle_render.text_units(title)*int(title_node.get('font-size')),600)
        self.assertLessEqual(kindle_render.text_units(detail)*int(detail_node.get('font-size')),300)
        self.assertEqual(detail_node.get('text-anchor'),'end')

if __name__=='__main__':unittest.main()
