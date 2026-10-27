"""Role permissions, global reservations and deterministic public Phase 2/3 tools."""
from contextlib import nullcontext
import json
import threading
import time
import httpx
from contracts import DataQuery, ForecastRequest
from phases.phase_01_foundation.http import ServiceError
from phases.phase_02_data.store import DataStore, local_query
from phases.phase_03_risk.tools import RiskTools
from .memory import eligible

COLLECTIONS = {
    'trade': {'trade_flows'},
    'disruption': {'shortages', 'products', 'events'},
    'hospital': {'suppliers', 'purchase_orders', 'shipments', 'inventory', 'hospitals', 'consumption', 'approvals'},
}


class InvestigationTools:
    def __init__(self, bundle, request, trace, *, max_calls=24, timeout=45, client=None):
        if type(max_calls) is not int or not 1 <= max_calls <= 30 or not 0 < timeout <= 120:
            raise ValueError('Invalid execution limits')
        self.bundle = bundle.model_copy(deep=True)
        self.request, self.trace, self.client = request, trace, client
        self.registry = {e.evidence_id: e for e in bundle.evidence}
        self.calls, self.max_calls = 0, max_calls
        self.deadline = time.monotonic() + timeout
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        if request.mode == 'fixture' and bundle.mode != 'fixture':
            raise ValueError('Fixture requests require fixture dataset')
        if request.mode == 'fixture' and client is not None:
            raise ValueError('No network client in fixture mode')

    def remaining(self):
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise ServiceError('TASK_TIMEOUT')
        return self.deadline - time.monotonic()

    def reserve(self, role, name, arguments):
        with self.lock:
            self.remaining()
            if self.calls >= self.max_calls:
                raise ServiceError('TOOL_CALL_BUDGET')
            self.calls += 1
            self.trace.event(name, 'started', role=role, arguments=arguments, call=self.calls)

    def query(self, role, query):
        q = DataQuery.model_validate(query)
        if role not in COLLECTIONS or q.collection not in COLLECTIONS[role]:
            raise ServiceError('TOOL_PERMISSION_DENIED')
        self.reserve(role, 'data.query', q.model_dump(mode='json'))
        expected = local_query(self.bundle, q, mode='fixture' if self.request.mode == 'fixture' else 'cached')
        page = expected
        if self.request.mode == 'live-zetaris':
            # Only read existing registered views. No publication, schema changes or arbitrary SQL.
            client = self.client or httpx.Client(timeout=httpx.Timeout(min(10, self.remaining()), connect=3),
                                                follow_redirects=False)
            with nullcontext(client) if self.client else client:
                page = DataStore(client, self.bundle).query(q)
            if [i.model_dump(mode='json') for i in page.items] != [i.model_dump(mode='json') for i in expected.items]:
                raise ServiceError('DATASET_READ_MISMATCH')
        self.remaining()
        if len(page.model_dump_json().encode()) > 250_000:
            raise ServiceError('TOOL_RESPONSE_BUDGET')
        selected = [r for r in page.items if eligible(r.evidence.evidence_id, self.registry,
                                                      self.request.evidence_as_of)]
        excluded = sorted({r.evidence.evidence_id for r in page.items if r not in selected})
        self.trace.event('data.query', role=role, collection=q.collection, mode=page.mode,
                         evidence_ids=[r.evidence.evidence_id for r in selected],
                         excluded_evidence_ids=excluded, truncated=page.truncated,
                         returned_count=len(selected))
        return selected, excluded

    def calculate(self, role, name, arguments):
        if not ((role == 'trade' and name == 'risk.trade_exposure')
                or (role == 'hospital' and name == 'risk.scenario_comparison')):
            raise ServiceError('TOOL_PERMISSION_DENIED')
        if name == 'risk.trade_exposure':
            if arguments != {'context_as_of': self.request.evidence_as_of.date().isoformat()}:
                raise ServiceError('TOOL_ARGUMENT_DENIED')
            dependencies = list(self.bundle.trade_flows)
        else:
            req = ForecastRequest.model_validate(arguments)
            if (req.hospital_id not in {h.hospital_id for h in self.bundle.hospitals}
                    or req.horizon_days != self.request.horizon_days or req.overlay is not None
                    or req.product_id != 'sim-carboplatin-450' or req.scenario != 'disruption'
                    or req.arrival_shelf_life_days != 30):
                raise ServiceError('TOOL_ARGUMENT_DENIED')
            # Forecasts have a fixed scenario date, never silently shifted to the evidence cutoff.
            if self.request.evidence_as_of.date() < self.bundle.scenario_as_of:
                raise ServiceError('SCENARIO_AFTER_CUTOFF')
            dependencies = [r for c in ('inventory', 'consumption', 'shipments', 'purchase_orders',
                                        'approvals', 'suppliers', 'hospitals', 'products', 'events')
                            for r in getattr(self.bundle, c) if r.evidence.kind == 'simulated']
        if any(not eligible(r.evidence.evidence_id, self.registry, self.request.evidence_as_of)
               for r in dependencies):
            raise ServiceError('CALCULATION_EVIDENCE_UNAVAILABLE')
        self.reserve(role, name, arguments)
        risk = RiskTools(self.bundle, max_calls=1)
        result = (risk.trade_exposure(arguments['context_as_of']) if role == 'trade'
                  else risk.scenario_comparison(req))
        self.remaining()
        if any(not eligible(i, self.registry, self.request.evidence_as_of) for i in result.input_evidence_ids):
            raise ServiceError('CALCULATION_EVIDENCE_UNAVAILABLE')
        value = result.model_dump(mode='json')
        if len(json.dumps(value).encode()) > 250_000:
            raise ServiceError('TOOL_RESPONSE_BUDGET')
        self.trace.event(name, role=role, analysis_id=result.analysis_id,
                         algorithm_version=result.algorithm_version, evidence_ids=result.input_evidence_ids)
        return result
