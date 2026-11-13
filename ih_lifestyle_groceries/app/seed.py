import json, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import items_lib
st, ai, out = sys.argv[1:4]
items = items_lib.build(json.load(open(st)), json.load(open(ai)))
json.dump({'items': items}, open(out, 'w'), indent=1, ensure_ascii=False)
print(len(items), 'items;', sum(1 for i in items if i['fresh']), 'with proposed shelf days;',
      len(json.dumps(items_lib.fresh_days_fact(items))), 'bytes fact')
