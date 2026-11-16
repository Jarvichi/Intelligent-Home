"""Shopping-list operations through the existing HA todo integration.

The bridge preserves UIDs and completed items used by the existing sync. It
never sends notifications and performs no writes unless explicitly requested.
"""


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
