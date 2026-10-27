"""Session/dataset/cutoff-isolated H-MEM with recursive evidence availability checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from .models import Finding

HERE = Path(__file__).resolve().parent


def eligible(evidence_id, registry, cutoff, visiting=None):
    visiting = set() if visiting is None else set(visiting)
    if evidence_id not in registry or evidence_id in visiting:
        return False
    visiting.add(evidence_id)
    item = registry[evidence_id]
    known = item.version_available_at or item.observed_at
    return (known <= cutoff and (item.updated_on is None or item.updated_on <= cutoff.date())
            and all(eligible(p, registry, cutoff, visiting) for p in item.parent_ids))


class Memory:
    def __init__(self, root, request, bundle, trace, *, node=None):
        self.request, self.trace = request, trace
        self.registry = {e.evidence_id: e for e in bundle.evidence}
        self.scope = hashlib.sha256(json.dumps([
            request.session_id, bundle.dataset_version, request.evidence_as_of.isoformat(),
            request.historical, request.mode,
        ]).encode()).hexdigest()
        self.directory = Path(root).resolve() / self.scope
        self.node = node or os.environ.get('PHARMAX_NODE', 'node')
        self.lock = threading.Lock()

    def validate(self, value):
        finding = Finding.model_validate(value)
        if any(not eligible(i, self.registry, self.request.evidence_as_of) for i in finding.evidence_ids):
            raise ValueError('MEMORY_EVIDENCE_UNAVAILABLE')
        sources = sorted({self.registry[i].source for i in finding.evidence_ids})
        if sorted(set(finding.source_ids)) != sources:
            raise ValueError('MEMORY_SOURCE_MISMATCH')
        kinds = {self.registry[i].kind for i in finding.evidence_ids}
        if ((finding.kind == 'observed' and kinds != {'observed'})
                or ('simulated' in kinds and finding.kind == 'observed')):
            raise ValueError('MEMORY_TRUST_MISMATCH')
        return finding

    def exchange(self, query, findings=()):
        if not isinstance(query, str) or not 1 <= len(query) <= 500:
            raise ValueError('MEMORY_QUERY_BUDGET')
        values = [self.validate(f).model_dump(mode='json') for f in findings]
        if len(values) > 20:
            raise ValueError('MEMORY_WRITE_BUDGET')
        self.trace.event('hmem.exchange', 'started', query=query, evidence_ids=[
            i for f in values for i in f['evidence_ids']], write_count=len(values), scope=self.scope)
        payload = dict(persist_dir=str(self.directory), scope=self.scope, findings=values,
                       query=query, clock=int(self.request.evidence_as_of.timestamp() * 1000))
        with self.lock:
            try:
                proc = subprocess.run([self.node, '--experimental-transform-types',
                    '--disable-warning=ExperimentalWarning', str(HERE / 'bridge.mjs')],
                    input=json.dumps(payload), capture_output=True, text=True, timeout=10, check=False)
            except subprocess.TimeoutExpired:
                raise ValueError('MEMORY_TIMEOUT') from None
            except OSError:
                raise ValueError('MEMORY_RUNTIME_UNAVAILABLE') from None
            if proc.returncode or len(proc.stdout.encode()) > 250_000:
                raise ValueError('MEMORY_ENGINE_FAILED')
            result = json.loads(proc.stdout)
            # Revalidate persisted records, not just newly proposed writes.
            for item in result['result']['memories']:
                self.validate(json.loads(item['memory']['content']))
        self.trace.event('hmem.exchange', writes=result['writes'],
                         retrieval=result['result']['trace'], conflicts=result['conflicts'],
                         memory_ids=[m['memory']['id'] for m in result['result']['memories']],
                         ledger_stats=result['ledger_stats'])
        return result
