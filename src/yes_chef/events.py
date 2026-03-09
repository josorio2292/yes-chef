"""In-process pub/sub event bus for SSE streaming."""

import asyncio
from dataclasses import dataclass


@dataclass
class SSEEvent:
    event: str  # item_step_change | item_completed | item_failed | quote_completed
    data: dict


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, quote_id: str) -> asyncio.Queue:
        if quote_id not in self._subscribers:
            self._subscribers[quote_id] = []
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[quote_id].append(queue)
        return queue

    def unsubscribe(self, quote_id: str, queue: asyncio.Queue) -> None:
        if quote_id in self._subscribers:
            try:
                self._subscribers[quote_id].remove(queue)
            except ValueError:
                pass

    async def publish(self, quote_id: str, event: SSEEvent) -> None:
        for queue in self._subscribers.get(quote_id, []):
            await queue.put(event)
