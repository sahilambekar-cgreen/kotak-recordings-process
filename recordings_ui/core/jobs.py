"""In-memory job registry.

One process, one dict, one lock. Jobs live only as long as the server does --
this is a local single-user tool, not a service.
"""

import threading
import uuid
from collections import deque
from datetime import datetime

_jobs = {}
_lock = threading.Lock()


class Job:
    def __init__(self, kind, product, total):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind              # "fill" | "download"
        self.product = product        # "TW" | "CC"
        self.total = total
        self.done = 0
        self.downloaded = 0
        self.copied = 0
        self.skipped = 0
        self.failed = 0
        self.filled = 0
        self.state = "running"        # running | finished | cancelled | error
        self.message = ""
        self.output_path = None       # filled workbook, or output root
        self.failures = []            # [{row, reason, url}]
        self.log = deque(maxlen=200)  # recent lines, self-trimming
        self.started = datetime.now()
        self.cancel = threading.Event()
        self._lock = threading.Lock()

    def tick(self, status, line=None, failure=None):
        with self._lock:
            self.done += 1
            if status == "downloaded":
                self.downloaded += 1
            elif status == "copied":
                self.copied += 1
            elif status == "skipped":
                self.skipped += 1
            elif status == "failed":
                self.failed += 1
            elif status == "filled":
                self.filled += 1
            if failure:
                self.failures.append(failure)
            if line:
                self.log.append(line)

    def snapshot(self):
        with self._lock:
            pct = (self.done / self.total * 100) if self.total else 0.0
            elapsed = (datetime.now() - self.started).total_seconds()
            rate = self.done / elapsed if elapsed > 0 else 0
            remaining = (self.total - self.done) / rate if rate > 0 else None
            return {
                "id": self.id,
                "kind": self.kind,
                "product": self.product,
                "state": self.state,
                "message": self.message,
                "total": self.total,
                "done": self.done,
                "downloaded": self.downloaded,
                "copied": self.copied,
                "skipped": self.skipped,
                "failed": self.failed,
                "filled": self.filled,
                "percent": round(pct, 1),
                "elapsed": round(elapsed, 1),
                "eta": round(remaining, 1) if remaining is not None else None,
                "output_path": self.output_path,
                "failure_count": len(self.failures),
                "log": list(self.log)[-40:],
            }


def create(kind, product, total):
    job = Job(kind, product, total)
    with _lock:
        _jobs[job.id] = job
    return job


def get(job_id):
    with _lock:
        return _jobs.get(job_id)


def all_jobs():
    with _lock:
        return list(_jobs.values())
