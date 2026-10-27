"""Synchronous allowlisted tool runtime; reserve calls before dispatch."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from uuid import uuid4
from pydantic import ValidationError
from contracts import InvestigationEvent, ToolRequest, ToolResult
from .http import ServiceError


class JSONLTrace:
    """Adapted from CityPulse JSONLTrace: filter metadata, never log raw model text."""
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, event):
        # Typed event schema rejects arbitrary payloads, secrets and private reasoning fields.
        safe = InvestigationEvent.model_validate(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open('a') as stream:
            stream.write(safe.model_dump_json() + '\n')


class Runtime:
    def __init__(self, handlers, *, mode='live', max_calls=3, trace=None):
        self.handlers, self.mode, self.max_calls = handlers, mode, max_calls
        self.calls = 0
        self.events = []
        self.trace = trace

    def event(self, operation, status, **kwargs):
        ev = InvestigationEvent(event_id=str(uuid4()), timestamp=datetime.now(timezone.utc),
                                mode=self.mode, operation=operation, status=status, **kwargs)
        self.events.append(ev)
        if self.trace:
            self.trace(ev)
        return ev

    def execute(self, request):
        req = ToolRequest.model_validate(request)
        if self.calls >= self.max_calls:
            self.event(req.name, 'BLOCKED', request_id=req.request_id, error_code='TOOL_CALL_BUDGET')
            return ToolResult(request_id=req.request_id, status='BLOCKED', mode=self.mode,
                              error_code='TOOL_CALL_BUDGET'), []
        if req.name not in self.handlers:
            self.event(req.name, 'BLOCKED', request_id=req.request_id, error_code='TOOL_UNAVAILABLE')
            return ToolResult(request_id=req.request_id, status='BLOCKED', mode=self.mode,
                              error_code='TOOL_UNAVAILABLE'), []
        self.calls += 1
        started = time.monotonic()
        self.event(req.name + '.started', 'PASS', request_id=req.request_id)
        try:
            records = self.handlers[req.name](req.limit)
            ids = [r.evidence.evidence_id for r in records if hasattr(r, 'evidence')]
            status, error = 'PASS', None
        except ServiceError as exc:
            status, error, records, ids = ('BLOCKED' if exc.blocked else 'FAIL'), exc.code, [], []
        except (ValidationError, KeyError, TypeError, ValueError):
            status, error, records, ids = 'FAIL', 'RECORD_SCHEMA_INVALID', [], []
        self.event(req.name, status, request_id=req.request_id, evidence_ids=ids,
                   duration_ms=round((time.monotonic() - started)*1000), error_code=error)
        return ToolResult(request_id=req.request_id, status=status, mode=self.mode,
                          evidence_ids=ids, record_count=len(records), error_code=error), records


def save_report(report, output):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(mode='json'), indent=2) + '\n')
