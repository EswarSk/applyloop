import asyncio
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

class EventBus:
    def __init__(self):
        self.history = deque(maxlen=100)
        self.subscribers = set()

    async def publish(self, type, stage, status, message, data=None):
        event = dict(id=uuid4().hex, type=type, stage=stage, status=status,
                     message=message, created_at=datetime.now(timezone.utc).isoformat(), data=data or {})
        self.history.append(event)
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
        return event

    def clear(self):
        self.history.clear()
        for queue in self.subscribers:
            while not queue.empty():
                queue.get_nowait()
