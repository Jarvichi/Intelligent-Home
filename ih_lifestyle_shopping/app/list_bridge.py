"""Shopping-list operations through the existing HA todo integration.

The bridge preserves UIDs and completed items used by the existing sync. It
never sends notifications and performs no writes unless explicitly requested.
"""
import re


def quantity(text):
    match = re.fullmatch(r'(.*?)\s+[x×]\s?(\d+)(\s*\(.*\))?\s*', text, re.I)
    if match and match[1].strip():
        return (match[1] + (match[3] or '')).strip(), int(match[2]) or 1
    match = re.fullmatch(r'(\d+)\s?[x×]\s+(.+)', text, re.I)
    if match:
        return match[2].strip(), int(match[1]) or 1
    return text.strip(), 1


class ListError(ValueError):
    pass


class ShoppingList:
    def __init__(self, ha, entity):
        self.ha = ha
        self.entity = entity

    def items(self):
        opened = self.ha.get_items(self.entity, status='needs_action')
        completed = self.ha.get_items(self.entity, status='completed')
        if not isinstance(opened, list) or not isinstance(completed, list):
            raise ListError('Shopping list unavailable')
        return {'open': opened, 'completed': completed}

    def add(self, name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255 or any(ord(c) < 32 for c in name):
            raise ListError('Enter an item name')
        self.ha.call('todo', 'add_item', {'entity_id': self.entity, 'item': name.strip()})

    def set_completed(self, uid, completed):
        if not isinstance(uid, str) or not uid or not isinstance(completed, bool):
            raise ListError('Invalid item')
        current = self.items()
        source = current['open'] if completed else current['completed']
        if not any(item.get('uid') == uid for item in source):
            raise ListError('Item changed; reload the list')
        self.ha.call('todo', 'update_item', {'entity_id': self.entity, 'item': uid,
                                             'status': 'completed' if completed else 'needs_action'})

    def complete_group(self, uids):
        """Tick a displayed group after checking every UID; report uncertainty on failure."""
        if not isinstance(uids, list) or not uids or any(not isinstance(uid, str) or not uid for uid in uids) or len(set(uids)) != len(uids):
            raise ListError('Invalid item group')
        opened = self.items()['open']
        by_id = {row.get('uid'): row for row in opened}
        if any(uid not in by_id for uid in uids):
            raise ListError('Items changed; reload the list')
        # The caller must have displayed these as one group; never tick unrelated rows.
        names = [by_id[uid].get('summary') for uid in uids]
        if any(not isinstance(name, str) for name in names):
            raise ListError('Items changed; reload the list')
        battery = [re.fullmatch(r'Batteries: (\d+)\s*[×x]\s*(\S+)', name) for name in names]
        if all(battery):
            matched = len({m[2] for m in battery}) == 1
        elif any(battery):
            matched = False
        else:
            matched = len({quantity(name)[0].casefold() for name in names}) == 1
        if not matched:
            raise ListError('Items changed; reload the list')
        updated = []
        for uid in uids:
            try:
                self.ha.call('todo', 'update_item', {'entity_id': self.entity, 'item': uid, 'status': 'completed'})
            except Exception:
                return {'updated': updated, 'uncertain': uid, 'complete': False}
            updated.append(uid)
        return {'updated': updated, 'uncertain': None, 'complete': True}

    def adjust_quantity(self, uids, delta):
        """One explicit +/- action changes one UID, never merges or rewrites others."""
        if not isinstance(uids, list) or not uids or any(not isinstance(uid, str) or not uid for uid in uids) or len(set(uids)) != len(uids) or type(delta) is not int or delta not in (-1, 1):
            raise ListError('Invalid quantity change')
        opened = self.items()['open']
        by_id = {row.get('uid'): row for row in opened}
        if any(not isinstance(uid, str) or uid not in by_id for uid in uids):
            raise ListError('Items changed; reload the list')
        rows = [by_id[uid] for uid in uids]
        if any(not isinstance(row.get('summary'), str) or row['summary'].startswith('Batteries: ') for row in rows):
            raise ListError('Quantity is not available for these items')
        parsed = [(row, *quantity(row['summary'])) for row in rows]
        if len({name.casefold() for _, name, _ in parsed}) != 1:
            raise ListError('Items changed; reload the list')
        if delta == 1:
            row, name, count = parsed[0]
        else:
            candidates = [item for item in parsed if item[2] > 1]
            if not candidates:
                if len(parsed) == 1:
                    raise ListError('Quantity cannot be less than one')
                self.ha.call('todo', 'remove_item', {'entity_id': self.entity, 'item': parsed[-1][0]['uid']})
                return
            row, name, count = candidates[0]
        new_count = count + delta
        display = name + ' x' + str(new_count) if new_count > 1 else name
        self.ha.call('todo', 'update_item', {'entity_id': self.entity, 'item': row['uid'], 'rename': display})
