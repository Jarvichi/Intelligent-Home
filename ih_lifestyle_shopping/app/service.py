"""Shopping backend routes, with live list writes disabled by default."""
import layouts
import list_bridge


class App:
    def __init__(self, db, shopping, list_writes_enabled=False):
        self.db, self.shopping = db, shopping
        self.list_writes_enabled = list_writes_enabled is True

    def route(self, method, path, body=None):
        if method == 'GET' and path == '/healthz':
            return 200, {'ok': True}
        try:
            if method == 'GET' and path == '/layouts':
                doc, rev = layouts.read(self.db)
                return 200, {'layouts': doc, 'revision': rev}
            if method == 'POST' and path == '/layouts/import':
                if not isinstance(body, dict) or set(body) != {'layouts'}:
                    return 400, {'error': 'Store and aisle data required.'}
                rev = layouts.save(self.db, body['layouts'])
                return 200, {'ok': True, 'revision': rev}
            if method == 'PUT' and path == '/layouts':
                if not isinstance(body, dict) or set(body) != {'layouts', 'revision'} or not isinstance(body['revision'], str):
                    return 400, {'error': 'Reload layouts before saving.'}
                rev = layouts.save(self.db, body['layouts'], body['revision'])
                return 200, {'ok': True, 'revision': rev}
            if method == 'GET' and path == '/list':
                return 200, self.shopping.items()
            if method == 'POST' and path in ('/list/add', '/list/state', '/list/group', '/list/quantity'):
                if not self.list_writes_enabled:
                    return 403, {'error': 'Shopping list changes are not enabled.'}
                if not isinstance(body, dict):
                    return 400, {'error': 'Invalid request.'}
                if path == '/list/add':
                    self.shopping.add(body.get('name'))
                elif path == '/list/state':
                    self.shopping.set_completed(body.get('uid'), body.get('completed'))
                elif path == '/list/group':
                    return 200, self.shopping.complete_group(body.get('uids'))
                else:
                    self.shopping.adjust_quantity(body.get('uids'), body.get('delta'))
                return 200, {'ok': True}
        except layouts.LayoutConflict:
            return 409, {'error': 'Layouts changed. Reload before saving.'}
        except (layouts.LayoutError, list_bridge.ListError):
            return 400, {'error': 'Check the details or reload the list.'}
        except Exception:
            return 502, {'error': 'Shopping service unavailable. Reload before retrying.'}
        return 404, {'error': 'Not found.'}
