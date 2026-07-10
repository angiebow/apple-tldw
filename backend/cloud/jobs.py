"""SQLite-backed job store.

One row per job. Deliberately tiny and dependency-free (stdlib sqlite3) so the
dev scaffold runs anywhere; prod would move this to Postgres and the in-memory
queue to Redis, but the JobStore interface stays the same.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config

JOB_TYPES = ("transcribe", "bloopers", "clip", "merge")
STATUS = ("queued", "running", "done", "error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["params"] = json.loads(d["params"]) if d["params"] else {}
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d


class JobStore:
    def __init__(self, db_path: Optional[str] = None):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        self.db_path = db_path or os.path.join(config.DATA_DIR, "jobs.db")
        # check_same_thread=False: the worker threads share this connection guarded
        # by a lock (fine for the dev scaffold's low volume).
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id         TEXT PRIMARY KEY,
                    type       TEXT NOT NULL,
                    status     TEXT NOT NULL,
                    progress   REAL NOT NULL DEFAULT 0,
                    stage      TEXT,
                    media_key  TEXT NOT NULL,
                    params     TEXT,
                    result     TEXT,
                    error      TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def create(self, job_type: str, media_key: str, params: Dict[str, Any]) -> Dict[str, Any]:
        job_id = "job_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, type, status, progress, stage, media_key, "
                "params, result, error, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, job_type, "queued", 0.0, None, media_key,
                 json.dumps(params or {}), None, None, now, now),
            )
            self._conn.commit()
        return self.get(job_id)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        if "params" in fields:
            fields["params"] = json.dumps(fields["params"])
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"])
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {cols} WHERE id = ?",
                (*fields.values(), job_id),
            )
            self._conn.commit()

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
