"""A tiny synchronous pub/sub bus.

Deliberately not asyncio: every subscriber here is either a UI repaint or a log
write, both sub-millisecond. Keeping it synchronous removes a scheduler hop from
the hot path between "speech recognised" and "mouse clicked".
"""
from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

Listener = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._lock = threading.RLock()

    def on(self, topic: str, fn: Listener) -> Listener:
        with self._lock:
            self._listeners[topic].append(fn)
        return fn

    def off(self, topic: str, fn: Listener) -> None:
        with self._lock:
            if fn in self._listeners[topic]:
                self._listeners[topic].remove(fn)

    def emit(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            listeners = list(self._listeners[topic]) + list(self._listeners["*"])
        for fn in listeners:
            try:
                fn(payload) if topic != "*" else fn(payload)
            except Exception:  # a broken listener must never stall an action
                import logging

                logging.getLogger("jarvis.bus").exception("listener failed on %s", topic)


BUS = EventBus()

# Topic names kept as constants so typos fail loudly at import time.
STATE = "state"            # idle | listening | thinking | acting | speaking
HEARD = "heard"            # partial or final transcript
COMMAND = "command"        # a dispatched command string
RESULT = "result"          # ActionResult
SAY = "say"                # text to speak
ERROR = "error"
