"""Bounded-age, read-only Home Assistant device metadata snapshots."""
import threading
import time
from ihlib import ha

class Snapshot:
    def __init__(self,token,client=None,clock=time.monotonic):
        self.client=client or ha.HA(token=token,connect=lambda url:ha._connect(url,timeout=3))
        self.clock=clock;self.lock=threading.Lock();self.cached=None;self.at=0
    def read(self):
        with self.lock:
            now=self.clock()
            if self.cached is not None and now-self.at<60:return self.cached
            try:
                states=self.client.get('states',timeout=3)
                entities,devices,areas=self.client.ws_many([{'type':'config/entity_registry/list'},{'type':'config/device_registry/list'},{'type':'config/area_registry/list'}])
                if not all(isinstance(x,list) for x in (states,entities,devices,areas)):return None
                value={'states':states,'entities':entities,'devices':devices,'areas':areas}
                self.cached=value;self.at=now
                return value
            except Exception:return None
