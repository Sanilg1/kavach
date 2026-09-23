"""Background job runner.

    memory - in-process queue (local development, tests)
    sqs    - Amazon SQS (set KAVACH_QUEUE_URL): jobs survive crashes and redeploys, and any
             number of instances can consume the same queue.

Jobs are pipeline functions referenced by name, so a message is just {"fn": ..., "args": [...]}.
While a job runs its message visibility is extended (heartbeat); if the process dies the
message becomes visible again within VISIBILITY seconds and another worker picks it up.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Callable

from .config import settings

log = logging.getLogger("kavach.worker")

VISIBILITY = 300          # seconds a received job stays hidden without a heartbeat
HEARTBEAT = 60            # how often a running job extends its visibility
MAX_RECEIVES = 3          # poison-message guard: give up after this many deliveries


def _resolve(name: str) -> Callable:
    from . import pipeline

    fn = getattr(pipeline, name, None)
    if not callable(fn):
        raise ValueError(f"unknown job {name!r}")
    return fn


class MemoryWorker:
    durable = False

    def __init__(self, n: int):
        self.q: "queue.Queue[tuple[str, tuple]]" = queue.Queue()
        for i in range(n):
            threading.Thread(target=self._run, daemon=True, name=f"kavach-worker-{i}").start()

    def submit(self, fn: Callable, *args) -> None:
        self.q.put((fn.__name__, args))

    def _run(self) -> None:
        while True:
            name, args = self.q.get()
            try:
                _resolve(name)(*args)
            except Exception:  # noqa: BLE001
                log.exception("job %s failed", name)
            finally:
                self.q.task_done()


class SqsWorker:
    durable = True

    def __init__(self, n: int, queue_url: str):
        import boto3

        self.url = queue_url
        self.sqs = boto3.client("sqs", region_name=settings.AWS_REGION)
        for i in range(n):
            threading.Thread(target=self._run, daemon=True, name=f"kavach-sqs-{i}").start()

    def submit(self, fn: Callable, *args) -> None:
        self.sqs.send_message(QueueUrl=self.url, MessageBody=json.dumps({"fn": fn.__name__, "args": list(args)}))

    def _heartbeat(self, receipt: str, stop: threading.Event) -> None:
        while not stop.wait(HEARTBEAT):
            try:
                self.sqs.change_message_visibility(QueueUrl=self.url, ReceiptHandle=receipt, VisibilityTimeout=VISIBILITY)
            except Exception as e:  # noqa: BLE001
                log.warning("heartbeat failed: %s", e)

    def _run(self) -> None:
        while True:
            try:
                resp = self.sqs.receive_message(
                    QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=20,
                    VisibilityTimeout=VISIBILITY, AttributeNames=["ApproximateReceiveCount"],
                )
            except Exception as e:  # noqa: BLE001 - network blips must not kill the thread
                log.warning("sqs receive failed: %s", e)
                time.sleep(5)
                continue
            for msg in resp.get("Messages", []):
                receipt = msg["ReceiptHandle"]
                body = json.loads(msg["Body"])
                count = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
                if count > MAX_RECEIVES:
                    log.error("dropping job %s after %d deliveries", body, count)
                    self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=receipt)
                    continue
                stop = threading.Event()
                threading.Thread(target=self._heartbeat, args=(receipt, stop), daemon=True).start()
                try:
                    if count > 1:
                        log.warning("resuming job %s (delivery %d)", body["fn"], count)
                    _resolve(body["fn"])(*body["args"])
                except Exception:  # noqa: BLE001 - the pipeline records failures in the DB
                    log.exception("job %s failed", body.get("fn"))
                finally:
                    stop.set()
                    # delete even on failure: failures are recorded, retrying would repeat them
                    try:
                        self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=receipt)
                    except Exception as e:  # noqa: BLE001
                        log.warning("delete failed: %s", e)


def _build():
    n = max(1, settings.WORKERS)
    if settings.QUEUE_URL:
        return SqsWorker(n, settings.QUEUE_URL)
    return MemoryWorker(n)


worker = _build()
