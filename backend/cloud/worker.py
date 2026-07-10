"""In-process job worker: a thread pool draining a queue, dispatching each job
to a registered handler by type.

In prod this becomes separate GPU worker processes pulling from Redis/Celery,
but the handler interface (`handle(ctx) -> Result`) is identical, so the ML
handlers written against it don't change.
"""
from __future__ import annotations

import os
import queue
import shutil
import tempfile
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional

from . import config
from .jobs import JobStore
from .storage import Storage


class Result:
    """What a handler returns: optional inline JSON `data` plus zero or more
    output objects already uploaded to storage."""

    def __init__(self, data: Optional[Dict[str, Any]] = None,
                 outputs: Optional[List[Dict[str, Any]]] = None):
        self.data = data or {}
        self.outputs = outputs or []


class JobContext:
    """Everything a handler needs, and nothing it doesn't: the job's params, its
    input media (already fetched to a local path), a scratch dir, storage for
    results, and a progress callback."""

    def __init__(self, job: Dict[str, Any], storage: Storage, store: JobStore,
                 workdir: str):
        self.job = job
        self.job_id = job["id"]
        self.type = job["type"]
        self.media_key = job["media_key"]
        self.params: Dict[str, Any] = job.get("params") or {}
        self.storage = storage
        self.workdir = workdir
        self._store = store

    def input_path(self) -> str:
        """Fetch the job's input media to the scratch dir and return its path."""
        ext = os.path.splitext(self.media_key)[1] or ".bin"
        dest = os.path.join(self.workdir, "input" + ext)
        return self.storage.fetch_to(self.media_key, dest)

    def result_key(self, name: str) -> str:
        return f"results/{self.job_id}/{name}"

    def put_output(self, local_path: str, kind: str, name: Optional[str] = None) -> Dict[str, Any]:
        """Upload a produced file to storage and return its output descriptor."""
        name = name or os.path.basename(local_path)
        key = self.result_key(name)
        self.storage.put_file(key, local_path)
        return {"key": key, "kind": kind, "bytes": self.storage.size(key)}

    def progress(self, fraction: float, stage: Optional[str] = None) -> None:
        frac = max(0.0, min(1.0, float(fraction)))
        self._store.update(self.job_id, progress=frac, stage=stage)


Handler = Callable[[JobContext], Result]

# Handler registry. Real ML handlers register themselves (see pipeline_handlers.py);
# until then every type falls back to the mock so the flow is testable end-to-end.
_HANDLERS: Dict[str, Handler] = {}


def register(job_type: str, handler: Handler) -> None:
    _HANDLERS[job_type] = handler


def mock_handler(ctx: JobContext) -> Result:
    """Type-agnostic stand-in: reports progress, then writes a canned result so
    the upload -> job -> poll -> download flow can be verified without any ML."""
    import time

    for i in range(1, 5):
        time.sleep(0.2)
        ctx.progress(i / 4.0, stage=f"mock {ctx.type} step {i}/4")

    # Prove input media was fetchable, then emit one canned output file.
    in_path = ctx.input_path()
    note = os.path.join(ctx.workdir, "mock_output.txt")
    with open(note, "w") as fh:
        fh.write(f"mock {ctx.type} result for {os.path.basename(in_path)}\n"
                 f"params={ctx.params}\n")
    output = ctx.put_output(note, kind="mock")
    return Result(data={"mock": True, "type": ctx.type}, outputs=[output])


class Worker:
    def __init__(self, store: JobStore, storage: Storage,
                 concurrency: Optional[int] = None):
        self.store = store
        self.storage = storage
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.concurrency = concurrency or config.WORKER_CONCURRENCY
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()

    def start(self) -> None:
        for n in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"worker-{n}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    def enqueue(self, job_id: str) -> None:
        self.queue.put(job_id)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._run(job_id)
            finally:
                self.queue.task_done()

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job:
            return
        handler = _HANDLERS.get(job["type"], mock_handler)
        workdir = tempfile.mkdtemp(prefix=f"{job_id}_")
        self.store.update(job_id, status="running", progress=0.0, stage="starting")
        try:
            ctx = JobContext(job, self.storage, self.store, workdir)
            result = handler(ctx)
            self.store.update(
                job_id, status="done", progress=1.0, stage="done",
                result={"data": result.data, "outputs": result.outputs},
            )
        except Exception as exc:  # any handler failure -> error state with reason
            self.store.update(
                job_id, status="error", stage="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            traceback.print_exc()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
