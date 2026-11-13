import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import items_lib as L

def test_norm():
    assert L.norm_key('Staples: Alpro almond unsweetened 1L x2') == 'alpro almond unsweetened 1l'
    assert L.norm_key('Meals: 2 chicken breasts (about 450 g)') == 'chicken breasts'

def test_shelf():
    assert L.shelf_days('ttd pastrami') == 5
    assert L.shelf_days('frozen raspberries') is None
    assert L.shelf_days('porridge oats') is None

def test_build():
    st = {'groups': {'lunch': {'items': ['TTD pastrami', 'ttd pastrami x2']}}}
    it = L.build(st, {'match': []})
    assert len(it) == 1 and it[0]['fresh'] and len(it[0]['aliases']) == 2
