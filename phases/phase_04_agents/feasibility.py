"""Deterministic constraint checks and Phase 3 recalculation, never physical execution."""
from datetime import datetime, time, timedelta, timezone
from contracts import (CandidateEvaluation, ConstraintFailure, HospitalMetrics, MitigationImpact,
                       MitigationCandidate, PurchaseOrder, Shipment, SupplyBalance)
from phases.phase_01_foundation.sources import digest
from phases.phase_03_risk.tools import RiskTools
from phases.phase_03_risk.shipments import compatible
from .memory import eligible
from .mitigation_scenario import seal, simulated_evidence

LEVELS = ('Low', 'Moderate', 'High', 'Critical')


def container_transfer_failures(operations, donor, target, before_target, anchor, permissions, unresolved):
    """Public bag-replay gate; legacy vial evaluations keep their original boundary."""
    failures = []
    if donor.total_unmet:
        failures.append('DONOR_SHORTAGE')
    if target.total_unmet >= before_target.total_unmet:
        failures.append('NO_IMPROVEMENT')
    if donor.hospital_id not in permissions or target.hospital_id not in permissions:
        failures.append('TRANSFER_NOT_AUTHORIZED')
    lots = [r for r in operations.inventory if r.hospital_id == donor.hospital_id and r.quality_status == 'released']
    if not lots or any(r.expiry_date < anchor+timedelta(days=31) for r in lots):
        failures.append('TRANSFER_LOT_EXPIRY_UNVERIFIED')
    if any(s.startswith(('ALLOCATION_ASN_CONFLICT', 'MATERIAL_UNRESOLVED_RECONCILIATION')) for s in unresolved):
        failures.append('UNRESOLVED_SUPPLY_EVIDENCE')
    return failures


def metrics(assessment):
    f = assessment.forecast
    return HospitalMetrics(hospital_id=assessment.hospital_id, unmet_vials=f.total_unmet,
        shortage_onset=f.projected_stockout_date, shortage_days=f.shortage_days,
        risk=assessment.rating.level, ending_usable=f.points[-1].closing_usable)


def evaluate(bundle, catalog, request, candidate, reference):
    """One immutable alternative. Invalid static constraints are never projected/applied."""
    c = MitigationCandidate.model_validate(candidate)
    before = {r.hospital_id: r.disruption for r in reference}
    if request.hospital_id not in before:
        raise ValueError('Unknown target hospital')
    original = before[request.hospital_id].forecast
    failures, ids = [], set(c.evidence_ids)
    registry = {e.evidence_id: e for e in bundle.evidence}
    anchor = datetime.combine(bundle.scenario_as_of, time.min, tzinfo=timezone.utc)
    route = next((r for r in catalog.routes if r.route_id == c.route_id), None)
    product = next((p for p in bundle.products if p.product_id == request.product_id), None)
    target = next((h for h in bundle.hospitals if h.hospital_id == request.hospital_id), None)
    donor = None
    available = 0
    source = None

    def fail(code, detail):
        failures.append(ConstraintFailure(code=code, detail=detail, evidence_ids=sorted(ids)))

    if c.hospital_id != request.hospital_id:
        fail('TARGET_MISMATCH', 'Candidate recipient differs from investigation target.')
    if c.product_id != request.product_id:
        fail('PRODUCT_MISMATCH', 'Exact product ID is required; no strength substitution is authorized.')
    if product is None or c.presentation != product.presentation:
        fail('PRESENTATION_MISMATCH', 'Exact eligible presentation is required.')
    if product:
        ids.add(product.evidence.evidence_id)
    if target:
        ids.add(target.evidence.evidence_id)
    if route is None:
        fail('UNKNOWN_ROUTE', 'Delivery route is not in the approved catalog.')
    else:
        ids.add(route.evidence.evidence_id)
        if not route.authorized or route.hospital_id != c.hospital_id or route.product_id != c.product_id:
            fail('SOURCE_AUTHORIZATION', 'Route/product/recipient authorization is absent or mismatched.')
        if c.quantity > route.max_quantity:
            fail('ROUTE_CAPACITY', 'Requested quantity exceeds the modeled route capacity.')
        if (c.dispatch_on < route.earliest_dispatch or c.arrival_on < c.dispatch_on + timedelta(days=route.transit_days)
                or c.arrival_on < c.dispatch_on or c.dispatch_on < bundle.scenario_as_of):
            fail('INVALID_DELIVERY_DATE', 'Dispatch or arrival violates scenario date/route lead time.')
        if product and (product.storage_min_c is None or product.storage_max_c is None
                or route.temperature_min_c < product.storage_min_c or route.temperature_max_c > product.storage_max_c
                or (product.protect_from_light and not route.protects_from_light)):
            fail('TRANSPORT_REQUIREMENTS', 'Transport temperature/light conditions do not meet product requirements.')
    if target is None or product is None or not compatible(product, target):
        fail('STORAGE_REQUIREMENTS', 'Recipient storage does not meet the exact product requirements.')
    if c.arrival_on > original.end_on or (original.projected_stockout_date and c.arrival_on > original.projected_stockout_date):
        fail('DELIVERY_TOO_LATE', 'Arrival is after first projected unmet demand or outside the forecast horizon.')
    if catalog.scenario_as_of != bundle.scenario_as_of or request.evidence_as_of < anchor:
        fail('SCENARIO_DATE_MISMATCH', 'Scenario start must be on/before the information cutoff.')
    if catalog.available_at > min(anchor, request.evidence_as_of):
        fail('EVIDENCE_AFTER_CUTOFF', 'Catalog assumptions were unavailable at the required scenario/cutoff time.')
    expiry = None
    supplier_id = None
    if c.kind == 'transfer':
        source = next((s for s in bundle.inventory if s.inventory_id == c.source_id), None)
        if c.dispatch_on != bundle.scenario_as_of:
            fail('FUTURE_TRANSFER_UNSUPPORTED', 'This pilot supports transfers dispatched at scenario start only.')
        if source:
            donor = source.hospital_id
            supplier_id, expiry = source.supplier_id, source.expiry_date
            ids.add(source.evidence.evidence_id)
            available = source.on_hand - source.reserved
            if donor == c.hospital_id:
                fail('TRANSFER_NOT_AUTHORIZED', 'Source and recipient must be different facilities.')
            if not route or not route.transfer_permitted or route.source_id != donor:
                fail('TRANSFER_NOT_AUTHORIZED', 'Facility-to-facility transfer authorization is required.')
    elif c.kind == 'supplier_delivery':
        source = next((s for s in catalog.supplier_stocks if s.stock_id == c.source_id), None)
        if source:
            supplier_id, expiry = source.supplier_id, source.expiry_date
            ids.add(source.evidence.evidence_id)
            available = source.on_hand - source.reserved
            if source.available_on > c.dispatch_on:
                fail('SOURCE_NOT_AVAILABLE', 'Supplier stock is not available at dispatch.')
            if not route or route.source_id != supplier_id:
                fail('SOURCE_AUTHORIZATION', 'Route is not authorized for this supplier.')
    else:
        source = next((s for s in bundle.shipments if s.shipment_id == c.source_id), None)
        if source:
            supplier_id = source.supplier_id
            ids.add(source.evidence.evidence_id)
            available = source.quantity
            if (c.quantity != source.quantity or c.hospital_id != source.hospital_id
                    or not route or route.source_id != supplier_id):
                fail('SOURCE_AUTHORIZATION', 'Rescheduling must preserve the original shipment quantity and recipient.')
            if source.status == 'held' and (not route or not route.release_hold_authorized):
                fail('HOLD_NOT_RELEASED', 'The modeled hold has no authorized release; rescheduling cannot assume one.')
            if c.dispatch_on != source.dispatched_on:
                fail('INVALID_DELIVERY_DATE', 'Rescheduling cannot rewrite the original dispatch date.')
            ids.update(next(r for r in reference if r.hospital_id == c.hospital_id).disruption.input_evidence_ids)
    if source is None:
        fail('UNKNOWN_SOURCE', 'No authoritative source lot, supplier stock or shipment exists.')
    else:
        if source.product_id != request.product_id:
            fail('PRODUCT_MISMATCH', 'Source product differs from the target product; no cross-strength conversion.')
        if c.kind != 'reschedule':
            source_product = next((p for p in bundle.products if p.product_id == source.product_id), None)
            if source_product is None or source_product.presentation != c.presentation:
                fail('PRESENTATION_MISMATCH', 'Proposed presentation does not match the source product.')
            if hasattr(source, 'presentation') and source.presentation != c.presentation:
                fail('PRESENTATION_MISMATCH', 'Supplier lot presentation does not match the proposal.')
            if source.quality_status != 'released':
                fail('QUARANTINED', 'Quarantined stock is unavailable for transfers or delivery.')
            if expiry < c.arrival_on:
                fail('EXPIRED', 'Source lot expires before proposed arrival.')
            elif expiry < c.arrival_on + timedelta(days=30):
                fail('INSUFFICIENT_SHELF_LIFE', 'Lot cannot support the engine\'s conservative 30-day arrival expiry.')
        if c.quantity > available:
            fail('INSUFFICIENT_UNRESERVED_STOCK', 'Quantity exceeds unreserved source inventory; reservations cannot move.')
        approvals = [a for a in bundle.approvals if
            (a.hospital_id, a.product_id, a.supplier_id) == (c.hospital_id, request.product_id, supplier_id)]
        ids.update(a.evidence.evidence_id for a in approvals)
        if not any(a.approved for a in approvals):
            fail('SOURCE_AUTHORIZATION', 'Recipient lacks approval for the source supplier/product.')
        if donor:
            donor_hospital = next(h for h in bundle.hospitals if h.hospital_id == donor)
            ids.add(donor_hospital.evidence.evidence_id)
            if product and not compatible(product, donor_hospital):
                fail('STORAGE_REQUIREMENTS', 'Donor storage does not meet the product requirements.')
    # Include all forecast dependencies; provenance cannot bypass a later parent revision.
    ids.update(i for r in reference for i in r.input_evidence_ids)
    if any(not eligible(i, registry, min(anchor, request.evidence_as_of)) for i in ids):
        fail('EVIDENCE_AFTER_CUTOFF', 'A required source, permission or parent revision is unavailable at the cutoff.')
    projected = {}
    projected_bundle = None
    balance = None
    if not failures:
        projected_bundle = bundle.model_copy(deep=True)
        ev = simulated_evidence(bundle, c.candidate_id, c.model_dump(mode='json'), ids)
        projected_bundle.evidence.append(ev)
        if c.kind == 'transfer':
            idx = next(i for i, s in enumerate(projected_bundle.inventory) if s.inventory_id == c.source_id)
            projected_bundle.inventory[idx] = source.model_copy(update={'on_hand': source.on_hand - c.quantity, 'evidence': ev})
        if c.kind == 'reschedule':
            idx = next(i for i, s in enumerate(projected_bundle.shipments) if s.shipment_id == c.source_id)
            projected_bundle.shipments[idx] = source.model_copy(update={'status': 'planned',
                'expected_arrival': c.arrival_on, 'temperature_min_c': route.temperature_min_c,
                'temperature_max_c': route.temperature_max_c, 'protects_from_light': route.protects_from_light,
                'evidence': ev})
        else:
            supplier = next(s for s in bundle.suppliers if s.supplier_id == supplier_id)
            po_id, ship_id = 'p4b-po-' + c.candidate_id, 'p4b-ship-' + c.candidate_id
            projected_bundle.purchase_orders.append(PurchaseOrder(purchase_order_id=po_id,
                hospital_id=c.hospital_id, supplier_id=supplier_id, product_id=c.product_id,
                quantity=c.quantity, ordered_on=bundle.scenario_as_of, expected_delivery=c.arrival_on, evidence=ev))
            projected_bundle.shipments.append(Shipment(shipment_id=ship_id, purchase_order_id=po_id,
                supplier_id=supplier_id, hospital_id=c.hospital_id, product_id=c.product_id, quantity=c.quantity,
                dispatched_on=c.dispatch_on, expected_arrival=c.arrival_on, status='planned',
                origin_country_iso=supplier.country_iso, temperature_min_c=route.temperature_min_c,
                temperature_max_c=route.temperature_max_c, protects_from_light=route.protects_from_light, evidence=ev))
        projected_bundle = seal(projected_bundle)
        risk = RiskTools(projected_bundle, max_calls=3)
        for h in bundle.hospitals:
            projected[h.hospital_id] = risk.hospital_risk({'hospital_id': h.hospital_id,
                'product_id': request.product_id, 'horizon_days': request.horizon_days})
        for hid, outcome in projected.items():
            if hid == request.hospital_id:
                continue
            original_forecast = before[hid].forecast
            if any(a.unmet > b.unmet for a, b in zip(outcome.forecast.points, original_forecast.points)):
                fail('DONOR_SHORTAGE', f'{hid} would incur additional unmet demand; preserving other facilities is required.')
            if LEVELS.index(outcome.rating.level) > LEVELS.index(before[hid].rating.level):
                fail('DONOR_RISK_WORSE', f'{hid} would have a worse risk category.')
        if projected[c.hospital_id].forecast.total_unmet >= original.total_unmet:
            fail('NO_IMPROVEMENT', 'This alternative does not reduce recipient unmet demand in the horizon.')
        balance = SupplyBalance(source_id=c.source_id, available_before=available, available_after=available-c.quantity,
            debited=c.quantity, recipient_receipt=c.quantity, existing_shipment_reused=c.kind == 'reschedule', conserved=True)
        # Verify the physical transfer debit and single recipient credit, independently of demand served.
        if c.kind == 'transfer':
            debit = sum(s.on_hand for s in bundle.inventory) - sum(s.on_hand for s in projected_bundle.inventory)
            if debit != c.quantity:
                raise ValueError('Transfer mass balance failed')
        if c.kind != 'reschedule':
            if sum(s.quantity for s in projected_bundle.shipments) - sum(s.quantity for s in bundle.shipments) != c.quantity:
                raise ValueError('Receipt mass balance failed')
    feasible = not failures
    if balance:
        balance.diagnostic_only = not feasible
    impacts = [MitigationImpact(hospital_id=hid, before=metrics(assessment),
        projected=projected.get(hid), effective=metrics(projected[hid]) if feasible else metrics(assessment),
        donor=hid == donor) for hid, assessment in sorted(before.items())]
    analyses = sorted({a.analysis_id for a in projected.values()})
    return CandidateEvaluation(evaluation_id=digest({'candidate': c.model_dump(mode='json'),
        'base': bundle.dataset_version, 'failures': [f.model_dump(mode='json') for f in failures], 'analyses': analyses}),
        candidate=c, feasible=feasible, failures=failures, original_unmet=original.total_unmet,
        remaining_unmet=projected[c.hospital_id].forecast.total_unmet if feasible else original.total_unmet,
        quantity_moved=c.quantity if feasible else 0, impacts=impacts, supply_balance=balance,
        projected_dataset_version=projected_bundle.dataset_version if projected_bundle else None,
        evidence_ids=sorted(ids), analysis_ids=analyses,
        projection_evidence=[ev] if projected_bundle else [])
