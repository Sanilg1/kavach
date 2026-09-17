"""Tiny in-process job queue. In the container deployment this is the background
worker; on Lambda the same pipeline functions would be invoked per job instead."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from .config import settings

log = logging.getLogger("kavach.worker")


class Worker:
    def __init__(self, n: int):
        self.q: "queue.Queue[tuple[Callable, tuple]]" = queue.Queue()
        self.threads = [threading.Thread(target=self._run, daemon=True, name=f"kavach-worker-{i}") for i in range(n)]
        for t in self.threads:
            t.start()

    def submit(self, fn: Callable, *args) -> None:
        self.q.put((fn, args))

    def _run(self) -> None:
        while True:
            fn, args = self.q.get()
            try:
                fn(*args)
            except Exception:  # noqa: BLE001
                log.exception("job %s failed", getattr(fn, "__name__", fn))
            finally:
                self.q.task_done()


worker = Worker(max(1, settings.WORKERS))
