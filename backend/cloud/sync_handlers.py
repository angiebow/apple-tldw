"""Synchronous, text-only endpoints (/highlight, /backsound) for the cloud app.

These carry no media and return fast-ish, so they stay synchronous (no job).
Rather than duplicate the ranking/backsound logic, we delegate to the functions
already in `server.py`. The import is deferred to call time so the cloud app
still boots without the heavy ML stack installed (e.g. local dev); a call
without the deps fails with a clear error instead of breaking startup.
"""
from __future__ import annotations

import sys
from typing import Any, Dict


def _server_module():
    """Import backend/server.py lazily. backend/ must be on sys.path (it is when
    the app runs as `cloud.app` from backend/, and the Docker image sets it)."""
    import server  # noqa: E402  (deferred: pulls in torch/transformers)
    return server


def run_highlight(payload: Dict[str, Any]) -> Dict[str, Any]:
    server = _server_module()
    return server.highlight(server.HighlightRequest(**payload))


def run_backsound(payload: Dict[str, Any]) -> Dict[str, Any]:
    server = _server_module()
    return server.backsound(server.BacksoundRequest(**payload))
