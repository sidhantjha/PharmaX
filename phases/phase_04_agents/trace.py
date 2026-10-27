"""Structured public activity only; never model messages, credentials or reasoning."""
import json
import threading
from .models import TraceEvent


class Trace:
    def __init__(self, emit=None):
        self.events = []
        self.emit = emit
        self.lock = threading.Lock()

    def event(self, operation, status='complete', **data):
        # Call sites supply closed typed arguments and projections, never raw exceptions/model output.
        with self.lock:
            event = TraceEvent(sequence=len(self.events) + 1, operation=operation, status=status, data=data)
            self.events.append(event)
            if self.emit:
                public = {k: v for k, v in data.items() if k != 'findings'}
                self.emit(f'[{event.sequence:02}] {operation} {status} ' + json.dumps(public, sort_keys=True))
                for finding in data.get('findings', []):
                    self.emit(f"  FINDING [{finding['kind']}] {finding['claim_key']}: {finding['value']}")
                    self.emit('    evidence=' + ','.join(finding['evidence_ids']))
                    self.emit('    sources=' + ','.join(finding['source_ids']) +
                              ' calculations=' + ','.join(finding['analysis_ids']))
                    self.emit('    limits=' + '; '.join(finding['limitations']))
