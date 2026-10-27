"""Bounded, deterministic semantic comparisons. No inference or network access."""
from datetime import timedelta
from itertools import combinations
from contracts.operational_claims import OperationalClaim, CorroborationFinding, EvidenceVerification
from phases.phase_01_foundation.sources import digest

IDENTITY = ('product_id', 'ndc', 'facility_id', 'account_id', 'order_id', 'line_id')
FULFILLMENT = {'shipped', 'delivered', 'accepted_receipt'}


def quantity(claim):
    return claim.quantity * (claim.case_bags if claim.unit == 'case' else 1)


def same_order(a, b):
    return all(getattr(a, k) is not None and getattr(a, k) == getattr(b, k) for k in IDENTITY)


def same_event(a, b):
    return same_order(a, b) and a.shipment_id is not None and a.shipment_id == b.shipment_id


def active_claims(claims, cutoff):
    """Only explicit, verified, same-origin updates supersede; time alone never does."""
    visible = [c for c in claims if c.available_at <= cutoff and c.issued_at <= cutoff
               and (c.effective_at <= cutoff or c.classification == 'projected')]
    by_id = {c.claim_id: c for c in visible}
    replaced = {}
    for c in visible:
        if not c.supersedes:
            continue
        old = by_id.get(c.supersedes)
        if not old or c.verification != 'verified' or c.effective_at <= old.effective_at:
            raise ValueError('INVALID_SUPERSESSION')
        if (c.meaning != old.meaning or c.originating_claim_id != old.originating_claim_id
                or c.organization != old.organization or c.shipment_id != old.shipment_id
                or any(getattr(c, k) != getattr(old, k) for k in IDENTITY)):
            raise ValueError('CROSS_EVENT_SUPERSESSION')
        if old.claim_id in replaced:
            raise ValueError('AMBIGUOUS_SUPERSESSION')
        replaced[old.claim_id] = c
    return [c for c in visible if c.claim_id not in replaced], replaced, visible


def reconcile(claims, cutoff, scenario='helene'):
    if len(claims) > 200:
        raise ValueError('CLAIM_BUDGET_EXCEEDED')
    claims = [OperationalClaim.model_validate(c) for c in claims]
    if len({c.claim_id for c in claims}) != len(claims):
        raise ValueError('DUPLICATE_CLAIM_ID')
    active, replaced, visible = active_claims(claims, cutoff)
    findings = []

    def add(category, records, reason, steps=(), discrepancy=0, review=False):
        values = dict(category=category, claim_ids=[c.claim_id for c in records],
                      evidence_ids=sorted({c.evidence.evidence_id for c in records}), reason=reason,
                      verification_steps=list(steps), discrepancy_bags=discrepancy,
                      independent_origins=len({c.originating_claim_id for c in records
                                               if c.observation != 'derived'}),
                      requires_human_review=review)
        findings.append(CorroborationFinding(finding_id='finding-'+digest(values)[:20], **values))

    for old_id, new in replaced.items():
        old = next(c for c in visible if c.claim_id == old_id)
        add('superseded', [old, new], 'Explicit verified same-origin operational update; old claim retained.')
    usable = [c for c in active if c.verification == 'verified' and c.classification != 'projected']
    for c in active:
        if c.verification != 'verified':
            add('missing', [c], 'Record is unverified or rejected; no physical supply credit.',
                ['Obtain authenticated source record and verification.'], review=True)
        if c.meaning == 'usable_inventory' and cutoff-c.effective_at > timedelta(hours=24):
            add('missing', [c], 'Inventory snapshot is older than 24 hours.',
                ['Obtain a fresh facility inventory and reservation snapshot.'], review=True)
    for a, b in combinations(usable, 2):
        if (a.originating_claim_id == b.originating_claim_id and a.meaning == b.meaning
                and (a.meaning not in FULFILLMENT or (quantity(a) == quantity(b)
                     and a.status == b.status and a.effective_at == b.effective_at and a.scope == b.scope))):
            add('compatible', [a, b], 'Shared originating claim; repeated publication is not independent confirmation.')
            continue
        if {a.meaning, b.meaning} == {'requested', 'allocation_policy'} and a.product_id == b.product_id:
            add('compatible', [a, b], 'Requested bags and account allocation policy describe different stages; '
                'the percentage cannot determine this order fill.',
                ['Obtain supplier acknowledgment with account entitlement and shipment reference.'])
            continue
        if a.shipment_id and a.shipment_id == b.shipment_id and not same_order(a, b):
            add('missing', [a, b], 'Shipment label matches but product, account, order line or facility identity does not.',
                ['Resolve original item master, order line and destination identifiers before comparison.'], review=True)
            continue
        if not same_event(a, b):
            continue
        if a.meaning == b.meaning and a.scope == b.scope == 'event_total':
            if a.effective_at != b.effective_at:
                add('missing', [a, b], 'Different effective times without explicit supersession; resolve event revision.',
                    ['Confirm event totals and obtain an explicitly linked correction.'], review=True)
            elif quantity(a) != quantity(b) or a.status != b.status:
                add('conflict', [a, b], 'Same event, semantic total and effective time have incompatible quantities/statuses.',
                    ['Obtain signed original event record and authorized correction.'],
                    abs(quantity(a)-quantity(b)), True)
            else:
                add('consistent', [a, b], 'Independent origins report the same operational event total.')
        elif a.meaning in FULFILLMENT and b.meaning in FULFILLMENT:
            add('compatible', [a, b], 'Different fulfillment stages or partial records require shipment-level aggregation; '
                'shipment or carrier delivery alone is not accepted hospital stock.')
    for po in [c for c in usable if c.meaning == 'requested']:
        linked = [c for c in usable if same_order(po, c)]
        if not any(c.meaning == 'acknowledged' for c in linked):
            add('missing', [po], 'Open order has no verified supplier acknowledgment; fill quantity remains unknown.',
                ['Request supplier acknowledgment, backorder balance and dated delivery commitment.'], review=True)
        if not any(c.meaning == 'accepted_receipt' for c in linked):
            add('missing', [po], 'No hospital-confirmed receipt; purchase order is not inventory.',
                ['Obtain receiving log and independently linked carrier proof of delivery.'], review=True)

    # Shipment gap only after its delivery window and a final cumulative receiving total.
    for ship in [c for c in usable if c.meaning == 'shipped']:
        receipts = [c for c in usable if c.meaning == 'accepted_receipt' and same_event(ship, c)]
        finals = [c for c in receipts if c.scope == 'event_total' and c.status == 'closed']
        if not receipts:
            add('missing', [ship], 'Supplier ship notice has no accepted hospital receiving evidence.',
                ['Request carrier delivery scan and hospital receiving/exception log.'], review=True)
        elif not finals or not ship.expected_by or cutoff <= ship.expected_by:
            add('compatible', [ship, *receipts], 'Partial receiving or delivery window remains open; '
                'remaining quantity is unconfirmed, not yet a contradiction.',
                ['Verify remaining packages, delivery window and receiving completion.'])
        elif any(r.effective_at >= ship.expected_by for r in finals):
            amounts = {quantity(r) for r in finals}
            if len(amounts) == 1:
                delta = quantity(ship)-next(iter(amounts))
                if delta:
                    add('conflict', [ship, *finals], 'Matched shipment and closed cumulative receiving after delivery '
                        'window leave an unexplained quantity difference; potential fulfillment discrepancy.',
                        ['Obtain carrier proof of delivery, package counts, damage/refusal and receiving exception logs.'],
                        abs(delta), True)
                else:
                    add('consistent', [ship, *finals], 'Shipped and accepted cumulative quantities reconcile after delivery.')

    # Do not double-count copies, partials beneath a cumulative total, or disputed receipts.
    blocked = {cid for f in findings if f.category == 'conflict' or
               (f.category == 'missing' and ('effective times' in f.reason or 'identity does not' in f.reason))
               for cid in f.claim_ids if next(c for c in visible if c.claim_id == cid).meaning == 'accepted_receipt'
               and 'potential fulfillment' not in f.reason}
    physical = {}
    groups = {}
    for r in usable:
        if r.meaning == 'accepted_receipt' and r.claim_id not in blocked and r.status in {'accepted', 'closed'}:
            groups.setdefault(tuple(getattr(r, k) for k in (*IDENTITY, 'shipment_id')), []).append(r)
    for rows in groups.values():
        if not any(c.observation == 'independent' for c in rows):
            add('missing', rows, 'Repeated receiving assertions lack an independently validated original receiving record.',
                ['Obtain the original hospital receiving record.'], review=True)
            continue
        totals = [c for c in rows if c.scope == 'event_total']
        chosen = totals[:1] if totals else list({c.originating_claim_id: c for c in rows}.values())
        physical[rows[0].shipment_id] = sum(quantity(c) for c in chosen)
    return EvidenceVerification(scenario=scenario, cutoff=cutoff, claims=visible, findings=findings,
        excluded_claim_ids=[c.claim_id for c in claims if c not in visible], physical_receipts=physical,
        conditional_forecasts=[], forecast_changes=[],
        requires_human_review=any(f.requires_human_review for f in findings),
        limitations=['Document identity is not independent fact verification.',
                     'All hospital and supplier transactions are synthetic.',
                     'Public-source historical custody remains unproven; reconstructed admission is explicit.',
                     'Conditional forecasts cannot authorize a physical action.'])
