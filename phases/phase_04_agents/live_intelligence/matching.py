"""Deterministic advisory exposure of a simulated, timed route; no observed delay inference."""
from contracts import DependencyMatch
from .registry import lifecycle


def intersects(a,b):
    return a.west <= b.east and b.west <= a.east and a.south <= b.north and b.south <= a.north


def match_dependency(signal, dependency, cutoff, *, available_signals=(), bundle=None):
    reasons, matched = [], []
    status = lifecycle(signal,cutoff,available_signals)
    if bundle is not None:
        from phases.phase_03_risk.shipments import compatible
        ship=next((s for s in bundle.shipments if s.shipment_id==dependency.shipment_id),None)
        product=next((p for p in bundle.products if p.product_id==dependency.product_id),None)
        hospital=next((h for h in bundle.hospitals if h.hospital_id==dependency.hospital_id),None)
        if (bundle.dataset_version!=dependency.dataset_version or ship is None or product is None or hospital is None
                or (ship.product_id,ship.hospital_id,ship.supplier_id,ship.quantity,ship.expected_arrival)!=
                   (dependency.product_id,dependency.hospital_id,dependency.supplier_id,dependency.quantity_vials,dependency.planned_arrival)
                or not compatible(product,hospital,ship)):
            return DependencyMatch(signal_revision=signal.revision_id,shipment_id=dependency.shipment_id,
                status='UNMATCHED',reasons=['Simulated shipment/product/storage dependency is inconsistent or incompatible.'],
                matched_legs=[],evidence_ids=signal.evidence_ids)
    if status in ('not-yet-available','expired','cancelled','superseded'):
        reasons.append('Signal is '+status+' at this information cutoff.')
        result = 'UNMATCHED'
    else:
        possible = False
        for leg in dependency.legs:
            # No speculative effect on a route leg already completed before the decision day.
            if leg.ends_on < cutoff.date():
                continue
            if signal.effective_on and leg.ends_on < signal.effective_on:
                continue
            if signal.expires_on and leg.starts_on > signal.expires_on:
                continue
            corridor = bool(set(leg.corridors) & set(signal.corridors))
            geo = any(intersects(leg.geography,g) for g in signal.geography)
            if corridor and geo:
                matched.append(leg.leg_id)
            elif corridor or geo:
                possible = True
        if signal.impacted_assets and not set(signal.impacted_assets)&{dependency.supplier_id,dependency.shipment_id}:
            possible=possible or bool(matched)
            matched=[]
        if matched and signal.confirmation == 'official_notice' and signal.effective_on and signal.expires_on:
            result = 'MATCHED'
            reasons.append('Official advisory validity, named corridor and coarse geography intersect simulated future/in-progress route legs.')
        elif matched or possible:
            result = 'POSSIBLE'
            reasons.append('Provisional geography, unverified media or incomplete timing requires corroboration; no automatic forecast change.')
        else:
            result = 'UNMATCHED'
            reasons.append('No eligible route corridor/geographic/time intersection; no forecast change.')
    reasons.append('Matching establishes modeled exposure only, not actual delay, a real supplier dependency or a carboplatin cargo.')
    return DependencyMatch(signal_revision=signal.revision_id,shipment_id=dependency.shipment_id,status=result,
        reasons=reasons,matched_legs=matched,evidence_ids=signal.evidence_ids)
