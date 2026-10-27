"""Approved mitigation specialist tools, extending Phase 4A's shared reservation budget."""
from datetime import datetime, time, timezone
import json
from typing import Literal
from pydantic import Field
from contracts import MitigationCandidate
from contracts.models import Contract
from phases.phase_01_foundation.http import ServiceError
from phases.phase_03_risk.tools import RiskTools
from .feasibility import evaluate
from .memory import eligible
from .tools import InvestigationTools


class InspectRequest(Contract):
    collection: Literal['supplier_inventory', 'hospital_lots', 'transfers', 'shipments', 'delivery_constraints', 'candidates']
    limit: int = Field(default=12, ge=1, le=20, strict=True)


class EvaluateRequest(Contract):
    candidate_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,160}$')


class MitigationTools(InvestigationTools):
    def __init__(self, bundle, catalog, request, trace, *, timeout=90, max_calls=20):
        # Mode remains cache-only even with live reasoning; operational evidence is always simulated.
        from .models import InvestigationRequest
        guard = InvestigationRequest(session_id=request.session_id, objective=request.objective,
            evidence_as_of=request.evidence_as_of, horizon_days=request.horizon_days, mode='cached')
        super().__init__(bundle, guard, trace, max_calls=max_calls, timeout=timeout)
        self.mitigation_request, self.catalog = request.model_copy(deep=True), catalog.model_copy(deep=True)
        self.reference = []

    def require_eligible(self, ids):
        anchor = datetime.combine(self.bundle.scenario_as_of, time.min, tzinfo=timezone.utc)
        cutoff = min(anchor, self.request.evidence_as_of)
        if any(not eligible(i, self.registry, cutoff) for i in ids) or self.catalog.available_at > cutoff:
            raise ServiceError('EVIDENCE_AFTER_CUTOFF')

    def baseline(self):
        dependencies = [r.evidence.evidence_id for name in ('hospitals', 'suppliers', 'products', 'approvals',
            'inventory', 'consumption', 'purchase_orders', 'shipments', 'events') for r in getattr(self.bundle, name)
            if r.evidence.kind == 'simulated']
        self.require_eligible(dependencies)
        self.reserve('hospital', 'mitigation.reference', {'scenario': self.mitigation_request.scenario,
                                                        'horizon_days': self.request.horizon_days})
        risk = RiskTools(self.bundle, max_calls=3)
        self.reference = [risk.scenario_comparison({'hospital_id': h.hospital_id,
            'product_id': self.mitigation_request.product_id, 'horizon_days': self.request.horizon_days})
            for h in self.bundle.hospitals]
        self.remaining()
        if sum(len(r.model_dump_json().encode()) for r in self.reference) > 500_000:
            raise ServiceError('TOOL_RESPONSE_BUDGET')
        self.trace.event('mitigation.reference', hospitals=[{
            'hospital_id': r.hospital_id, 'baseline': r.baseline.forecast.total_unmet,
            'disruption': r.disruption.forecast.total_unmet, 'shortage_onset': str(r.disruption.forecast.projected_stockout_date),
            'risk': r.disruption.rating.level, 'analysis_id': r.analysis_id} for r in self.reference])
        return self.reference

    def inspect(self, arguments):
        req = InspectRequest.model_validate(arguments)
        self.reserve('supply', 'mitigation.inspect', req.model_dump())
        selected = {
            'supplier_inventory': self.catalog.supplier_stocks,
            'hospital_lots': self.bundle.inventory,
            'transfers': [r for r in self.catalog.routes if r.transfer_permitted],
            'shipments': self.bundle.shipments,
            'delivery_constraints': self.catalog.routes,
            'candidates': self.catalog.candidates,
        }[req.collection][:req.limit]
        ids = [i for r in selected for i in (r.evidence_ids if isinstance(r, MitigationCandidate)
                                            else [r.evidence.evidence_id])]
        self.require_eligible(ids)
        value = [r.model_dump(mode='json') for r in selected]
        if len(json.dumps(value).encode()) > 100_000:
            raise ServiceError('TOOL_RESPONSE_BUDGET')
        self.remaining()
        self.trace.event('mitigation.inspect', collection=req.collection, records=value,
                         evidence_ids=sorted(set(ids)), simulated=True)
        return [r.model_copy(deep=True) for r in selected]

    def check(self, arguments):
        req = EvaluateRequest.model_validate(arguments)
        candidate = next((c for c in self.catalog.candidates if c.candidate_id == req.candidate_id), None)
        if candidate is None:
            raise ServiceError('UNKNOWN_CANDIDATE')
        self.require_eligible(candidate.evidence_ids)
        self.reserve('feasibility', 'mitigation.evaluate', req.model_dump())
        if not self.reference:
            raise ServiceError('REFERENCE_REQUIRED')
        result = evaluate(self.bundle, self.catalog, self.mitigation_request, candidate, self.reference)
        self.remaining()
        if len(result.model_dump_json().encode()) > 500_000:
            raise ServiceError('TOOL_RESPONSE_BUDGET')
        self.trace.event('mitigation.evaluate', candidate_id=candidate.candidate_id,
            feasible=result.feasible, rejection_codes=[f.code for f in result.failures],
            reasons=[f.detail for f in result.failures], original_unmet=result.original_unmet,
            remaining_unmet=result.remaining_unmet, quantity_moved=result.quantity_moved,
            impacts=[{'hospital_id': i.hospital_id, 'before': i.before.model_dump(mode='json'),
                'effective': i.effective.model_dump(mode='json'),
                'diagnostic_projected_unmet': i.projected.forecast.total_unmet if i.projected else None}
                for i in result.impacts], evidence_ids=result.evidence_ids, analysis_ids=result.analysis_ids)
        return result
