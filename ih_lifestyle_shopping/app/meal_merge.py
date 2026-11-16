"""Review meal shopping additions through existing data owners.

No messages, list changes or ledger writes are made. Applying the preview is
not available until the Cooking ledger handover can be verified safely.
"""
import hashlib
import json

DAYS = ('mon','tue','wed','thu','fri','sat','sun')
SPECIAL = {'', '-', 'freestyle', 'eating out', 'takeaway', 'leftovers'}


class MealError(ValueError):
    pass


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


class MealShopping:
    def __init__(self, cooking, shopping):
        self.cooking, self.shopping = cooking, shopping

    def _snapshot(self, groups, include_completed):
        if not isinstance(groups, list) or any(not isinstance(g,str) or not g for g in groups) or len(set(groups)) != len(groups) or not isinstance(include_completed,bool):
            raise MealError('Invalid shopping choices')
        plan = self.cooking.plan()
        recipes = self.cooking.recipes()
        staples = self.cooking.staples()
        current = self.shopping.items()
        if not all(isinstance(x,dict) for x in (plan,recipes,staples,current)):
            raise MealError('Meal plan unavailable')
        known_groups = staples.get('groups')
        if not isinstance(known_groups,dict) or any(group not in known_groups for group in groups):
            raise MealError('Staple groups changed; reload')
        names = {}
        for slug, recipe in recipes.items():
            if not isinstance(slug,str) or not isinstance(recipe,dict):
                raise MealError('Recipe list unavailable')
            for name in (slug, recipe.get('title'), recipe.get('name')):
                if isinstance(name,str) and name.strip(): names.setdefault(name.strip().casefold(),set()).add(slug)
        picks=[]
        for day in DAYS:
            value=plan.get(day,'')
            if not isinstance(value,str): raise MealError('Invalid meal plan')
            lookup=value.strip().casefold()
            if lookup in SPECIAL: continue
            matches=names.get(lookup,set())
            if len(matches)!=1: raise MealError('A planned meal is missing or ambiguous')
            slug=next(iter(matches))
            if slug not in picks:picks.append(slug)
        open_items=current.get('open')
        complete_items=current.get('completed')
        if not isinstance(open_items,list) or not isinstance(complete_items,list):
            raise MealError('Shopping list unavailable')
        considered=open_items + (complete_items if include_completed else [])
        if any(not isinstance(item,dict) or not isinstance(item.get('summary'),str) or not item.get('uid') for item in considered):
            raise MealError('Shopping list changed; reload')
        return {'plan':plan,'recipes':recipes,'staples':staples,'items':considered,'groups':groups,
                'include_completed':include_completed,'picks':picks}

    def preview(self, groups, include_completed):
        before=self._snapshot(groups,include_completed)
        have=[row['summary'] for row in before['items']]
        proposal=self.cooking.merge(before['picks'],groups,have,False)
        if not isinstance(proposal,dict) or proposal.get('ledger_written') is not False or not isinstance(proposal.get('add'),list) or any(not isinstance(x,str) for x in proposal['add']):
            raise MealError('Shopping preview unavailable')
        after=self._snapshot(groups,include_completed)
        if _fingerprint(before)!=_fingerprint(after):
            raise MealError('Plan or list changed; reload')
        return {'add':proposal['add'],'skipped':proposal.get('skipped',[]),'revision':_fingerprint(before)}

