"""Append-only local simulation; transactional lot reservations and conservation."""
from datetime import datetime, timezone, date, timedelta
from hashlib import sha256
import json
import csv
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
from contracts.models import SourceEvidence
from contracts.operations import ActionProposal, ActionTransition
from contracts.replay import ScenarioOperations
from phases.phase_01_foundation.sources import digest
from phases.phase_06_helene.pipeline import forecast_operations
from phases.phase_07_reconciliation.pipeline import verify_case
from .ingestion import import_csv

TERMINAL = {'CLOSED', 'REJECTED', 'CANCELLED', 'EXPIRED'}
TRANSITIONS = {
    'PROPOSED': ['EVIDENCE_REVIEW', 'REJECTED', 'CANCELLED', 'EXPIRED'],
    'EVIDENCE_REVIEW': ['AUTHORIZATION_PENDING', 'INFORMATION_REQUESTED', 'REJECTED', 'CANCELLED', 'EXPIRED'],
    'INFORMATION_REQUESTED': ['EVIDENCE_REVIEW', 'CANCELLED', 'REJECTED', 'EXPIRED'],
    'AUTHORIZATION_PENDING': ['AUTHORIZED', 'INFORMATION_REQUESTED', 'REJECTED', 'CANCELLED', 'EXPIRED'],
    'AUTHORIZED': ['REQUEST_PREPARED', 'CANCELLED', 'EXPIRED'],
    'REQUEST_PREPARED': ['SIMULATED_DISPATCH', 'CANCELLED', 'EXPIRED'],
    'SIMULATED_DISPATCH': ['SIMULATED_RECEIPT'],
    'SIMULATED_RECEIPT': ['SIMULATED_RECEIPT', 'CLOSED'],
}
POLICY = {
    'source': 'https://www.fda.gov/drugs/drug-supply-chain-security-act-dscsa/title-ii-drug-quality-and-security-act',
    'assessment': 'IV fluids/electrolyte replenishment has specific DSCSA exclusions; do not require generic serialized tracing for this saline presentation.',
    'required_review': 'Pharmacist must determine applicable state, facility, storage, release and transfer policy. No automatic compliance finding.',
    'handling': 'Verify sealed intact VIAFLEX presentation, label storage and transport conditions; no clinical substitution.',
}


class WorkflowStore:
    def __init__(self, base):
        self.base = base
        with base.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS op_workspaces (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS op_events (id TEXT, revision INTEGER, workspace TEXT, payload TEXT NOT NULL, PRIMARY KEY(id,revision))')
            for table in ('op_workspaces', 'op_events'):
                for op in ('UPDATE', 'DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable'); END")

    def workspace(self, case):
        # Verify retained originals before reuse, never trust changed raw records.
        for e in case.evidence:
            if e.raw_ref and sha256(Path(e.raw_ref).read_bytes()).hexdigest() != e.content_sha256:
                raise ValueError('SOURCE_HASH_CHANGED_REIMPORT_REQUIRED')
        folders = {Path(r.source_path).parent for r in case.admission.imports}
        if len(folders) != 1:
            raise ValueError('UNSUPPORTED_IMPORT_SET')
        folder = folders.pop()
        ops, receipts, readiness = import_csv(folder, case.checkpoint.cutoff)
        prior = {r.feed: r for r in case.admission.imports}
        if any(r.feed not in prior or any(getattr(r, field) != getattr(prior[r.feed], field)
            for field in ('content_sha256', 'exported_at', 'effective_at', 'modeled_available_at')) for r in receipts):
            raise ValueError('IMPORT_CLOCK_OR_HASH_CHANGED')
        permissions = [r['facility_id'] for r in csv.DictReader((folder / 'suppliers.csv').read_text().splitlines())
                       if r['transfers_allowed'] == 'true']
        files = {feed: list(csv.DictReader((folder/(feed+'.csv')).read_text().splitlines()))
                 for feed in ('inventory', 'catalog', 'purchase_orders', 'acknowledgments', 'shipments', 'purchasing', 'consumption', 'facilities', 'suppliers')}
        verification = case.verification or verify_case(case, files)
        # Phase 7 receipts are admitted by its existing reconciler and already in case.operations.
        if case.verification:
            ops = case.operations
        # Canonical snapshot identity prevents reformatting/reimport from resetting reservations.
        key = digest(sorted((r['facility_id'], ops.products[0].product_id, r['lot_id'], r['snapshot_at'])
                            for r in files['inventory']))
        payload = dict(operations=ops.model_dump(mode='json'), readiness=readiness,
            anchor=str(case.forecasts[0].as_of), verification=verification.model_dump(mode='json'),
            case_id=case.investigation_id, product_id=ops.products[0].product_id, permissions=permissions,
            supplier_ids={r['facility_id']:r['supplier_id'] for r in files['catalog']},
            # Same stock export may not be reused with different demand, quality or receipt overlays.
            basis=digest(ops.model_dump(mode='json')))
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload FROM op_workspaces WHERE id=?', (key,)).fetchone()
            if old and json.loads(old[0])['basis'] != payload['basis']:
                raise ValueError('INVENTORY_WORKSPACE_BASIS_CONFLICT')
            if not old:
                db.execute('INSERT INTO op_workspaces VALUES (?,?)', (key, json.dumps(payload)))
        retained = json.loads(old[0]) if old else payload
        # Additive reader compatibility for earlier local Phase 8 validation snapshots.
        retained.setdefault('supplier_ids', payload['supplier_ids'])
        return key, retained

    def latest(self, db, workspace):
        rows = db.execute('SELECT payload FROM op_events e WHERE workspace=? AND revision=(SELECT MAX(revision) FROM op_events WHERE id=e.id)', (workspace,))
        return [json.loads(r[0]) for r in rows]

    def operations(self, payload, events, *, reserved=False, include_receipts=True):
        ops = ScenarioOperations.model_validate(payload['operations'])
        rows = [r.model_dump(mode='python') for r in ops.inventory]
        for action in events:
            p = action['proposal']
            if p['kind'] == 'transfer':
                debit = action['dispatched']
                if reserved and action['state'] not in TERMINAL:
                    debit += action['quantity_bags'] - action['dispatched']
                if debit:
                    row = next(r for r in rows if r['hospital_id'] == p['donor'] and r['lot_id'] == p['lot_id'])
                    row['on_hand'] -= debit
            for receipt in action['receipts']:
                if receipt['quantity'] and include_receipts:
                    rows.append(dict(inventory_id=receipt['receipt_id'], hospital_id=p['recipient'],
                        product_id=p['product_id'], supplier_id=payload['supplier_ids'][p['recipient']],
                        lot_id=p['lot_id'], as_of=ops.inventory[0].as_of,
                        on_hand=receipt['quantity'], reserved=0, expiry_date=receipt['expires_on'],
                        quality_status='released' if receipt['quality'] == 'released' else 'quarantined',
                        quantity_unit='bag', evidence=SourceEvidence.model_validate(receipt['evidence'])))
        return ScenarioOperations.model_validate(dict(ops.model_dump(mode='python'), inventory=rows))

    def forecasts(self, payload, events, *, reserved=False):
        ops = self.operations(payload, events, reserved=reserved, include_receipts=False)
        supplies = [SimpleNamespace(hospital_id=a['proposal']['recipient'], quantity=r['quantity'],
            modeled_arrival=date.fromisoformat(a['proposal']['expected_on']), shipment_id=r['receipt_id'],
            lot_id=a['proposal']['lot_id'], expiry_date=date.fromisoformat(r['expires_on']),
            quarantined=r['quality'] != 'released', evidence_id=r['evidence']['evidence_id'])
            for a in events for r in a['receipts'] if r['quantity']]
        return forecast_operations(ops, date.fromisoformat(payload['anchor']), supplies, digest(events))

    def validate_transfer(self, proposal, quantity, payload, events):
        ops = self.operations(payload, events, reserved=True)
        if proposal.product_id != payload['product_id']:
            raise ValueError('UNSUPPORTED_PRODUCT_SUBSTITUTION')
        if proposal.recipient not in {h.hospital_id for h in ops.hospitals}:
            raise ValueError('RECIPIENT_NOT_IN_NETWORK')
        if not date.fromisoformat(payload['anchor']) <= proposal.expected_on <= date.fromisoformat(payload['anchor']) + timedelta(days=13):
            raise ValueError('TRANSFER_OUTSIDE_FORECAST_HORIZON')
        if proposal.kind == 'replenishment':
            if proposal.donor:
                raise ValueError('SUPPLIER_REQUEST_HAS_NO_DONOR')
            return
        if proposal.donor == proposal.recipient:
            raise ValueError('INVALID_DONOR')
        if not {proposal.donor, proposal.recipient} <= set(payload['permissions']):
            raise ValueError('FACILITY_TRANSFER_PERMISSION_REQUIRED')
        if any(f['category'] == 'conflict' for f in payload['verification']['findings']):
            raise ValueError('UNRESOLVED_SUPPLY_EVIDENCE')
        matching = [r for r in ops.inventory if r.hospital_id == proposal.donor and r.lot_id == proposal.lot_id
                    and r.product_id == proposal.product_id]
        if len(matching) != 1:
            raise ValueError('WRONG_PRODUCT_OR_LOT')
        lot = matching[0]
        if lot.quality_status != 'released' or lot.expiry_date < proposal.expected_on:
            raise ValueError('UNUSABLE_LOT')
        if lot.on_hand - lot.reserved < quantity:
            raise ValueError('INSUFFICIENT_UNRESERVED_LOT')
        before = self.forecasts(payload, events, reserved=True)
        donor = next(f for f in before if f.hospital_id == proposal.donor)
        recipient = next(f for f in before if f.hospital_id == proposal.recipient)
        # Conservative reserve: all 14 days of demand protected without assuming inbound orders.
        if donor.total_unmet or donor.points[-1].closing_usable < quantity:
            raise ValueError('DONOR_MINIMUM_RESERVE')
        if quantity > recipient.total_unmet:
            raise ValueError('QUANTITY_EXCEEDS_RECIPIENT_DEMAND')
        # The same engine verifies availability at transfer time, after intervening consumption.
        point = next(p for p in donor.points if p.day == proposal.expected_on)
        if point.opening_physical - point.reserved < quantity:
            raise ValueError('UNAVAILABLE_AT_TRANSFER_TIME')

    def propose(self, case, request):
        p = ActionProposal.model_validate(request)
        workspace, payload = self.workspace(case)
        units = {i['original']['source_organization']: i['units_per_case'] for i in payload['readiness']['identities']}
        if p.units_per_case != units.get(p.donor if p.kind == 'transfer' else p.recipient):
            raise ValueError('INCORRECT_PACKAGE_CONVERSION')
        quantity = p.quantity * (p.units_per_case if p.unit == 'case' else 1)
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self.latest(db, workspace)
            self.validate_transfer(p, quantity, payload, events)
            action = dict(id='op-' + uuid4().hex, case_id=case.investigation_id, workspace=workspace,
                revision=1, state='PROPOSED', proposal=p.model_dump(mode='json'), quantity_bags=quantity,
                dispatched=0, receipts=[], received=0, policy=POLICY, authorization=None,
                audit=[dict(actor=p.actor, state='PROPOSED', at=datetime.now(timezone.utc).isoformat())],
                classification='simulated; no physical fulfillment', evidence_ids=[e.evidence_id for e in case.evidence],
                supporting_sources=[dict(evidence_id=e.evidence_id, source=str(e.source_url),
                    sha256=e.content_sha256, kind=e.kind) for e in case.evidence],
                baseline_forecasts=[dict(hospital_id=f.hospital_id, total_unmet=f.total_unmet)
                    for f in self.forecasts(payload, events)])
            self.append(db, action)
        return action

    def append(self, db, action):
        db.execute('INSERT INTO op_events VALUES (?,?,?,?)',
            (action['id'], action['revision'], action['workspace'], json.dumps(action)))

    def transition(self, case, identity, request):
        req = ActionTransition.model_validate(request)
        workspace, payload = self.workspace(case)
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self.latest(db, workspace)
            a = next((a for a in events if a['id'] == identity), None)
            if a is None:
                raise KeyError('ACTION_NOT_FOUND')
            if a['revision'] != req.expected_revision:
                raise ValueError('REVISION_CONFLICT')
            if not req.attested or not req.actor.strip() or req.state not in TRANSITIONS.get(a['state'], []):
                raise ValueError('DISALLOWED_TRANSITION_OR_ATTESTATION')
            required = 'pharmacist' if req.state == 'AUTHORIZED' else 'receiving' if req.state == 'SIMULATED_RECEIPT' else 'operator'
            if req.role != required:
                raise ValueError('ROLE_NOT_AUTHORIZED')
            if req.state == 'AUTHORIZED':
                if not req.policy_reference or not req.policy_reference.strip():
                    raise ValueError('PHARMACY_POLICY_REVIEW_REQUIRED')
                if req.actor == a['proposal']['actor']:
                    raise ValueError('INDEPENDENT_PHARMACY_REVIEW_REQUIRED')
                a['authorization'] = dict(actor=req.actor, policy_reference=req.policy_reference,
                    role=req.role, attested=True, scope='local simulation only; identity not authenticated')
            if req.state in {'REQUEST_PREPARED', 'SIMULATED_DISPATCH', 'SIMULATED_RECEIPT', 'CLOSED'} and not a['authorization']:
                raise ValueError('MISSING_PHARMACY_AUTHORIZATION')
            if req.state in {'REQUEST_PREPARED', 'SIMULATED_DISPATCH'}:
                others = [v for v in events if v['id'] != a['id']]
                self.validate_transfer(ActionProposal.model_validate(a['proposal']), a['quantity_bags'], payload, others)
            if req.state == 'SIMULATED_DISPATCH':
                a['dispatched'] = a['quantity_bags']
                a['dispatch_record'] = dict(record_id='sim-dispatch-'+a['id'],
                    kind='synthetic supplier acknowledgment and dispatch' if a['proposal']['kind']=='replenishment' else 'synthetic interfacility dispatch',
                    lot_id=a['proposal']['lot_id'], quantity=a['quantity_bags'], unit='bag',
                    expected_on=a['proposal']['expected_on'], source_action=a['id'],
                    authorization=a['authorization'], classification='simulated; no external confirmation')
            if req.state == 'SIMULATED_RECEIPT':
                if (req.received_quantity is None or not req.receipt_id or not req.expires_on
                    or not req.quality or req.lot_id != a['proposal']['lot_id']):
                    raise ValueError('RECEIPT_FIELDS_REQUIRED')
                if any(r['receipt_id'] == req.receipt_id for e in events for r in e['receipts']):
                    raise ValueError('DUPLICATE_RECEIPT')
                if req.expires_on < date.fromisoformat(a['proposal']['expected_on']):
                    raise ValueError('EXPIRED_RECEIPT')
                if a['proposal']['kind'] == 'transfer':
                    original = ScenarioOperations.model_validate(payload['operations'])
                    lot = next(r for r in original.inventory if r.hospital_id == a['proposal']['donor'] and r.lot_id == req.lot_id)
                    if req.expires_on != lot.expiry_date:
                        raise ValueError('LOT_EXPIRY_MISMATCH')
                if a['received'] + req.received_quantity > a['dispatched']:
                    raise ValueError('RECEIPT_EXCEEDS_DISPATCH')
                receipt = dict(receipt_id=req.receipt_id, quantity=req.received_quantity,
                    expires_on=str(req.expires_on), quality=req.quality,
                    classification='synthetic receiving record', note=req.note)
                ev = SourceEvidence(evidence_id='sim-receipt-'+req.receipt_id, kind='simulated',
                    source='Phase 8 synthetic receiving ledger', source_record_id=req.receipt_id,
                    source_url='https://example.invalid/pharmax/receiving/'+req.receipt_id,
                    observed_at=datetime.now(timezone.utc), content_sha256=digest(receipt),
                    parent_ids=a['evidence_ids'], note='Synthetic record persisted in op_events; logical arrival '+a['proposal']['expected_on'])
                receipt['evidence'] = ev.model_dump(mode='json')
                a['receipts'].append(receipt)
                a['received'] += req.received_quantity
            if req.state == 'CLOSED':
                a['discrepancy_bags'] = a['dispatched'] - a['received']
                a['disposition'] = req.note
            a['state'] = req.state
            a['revision'] += 1
            a['audit'].append(dict(**req.model_dump(mode='json'), at=datetime.now(timezone.utc).isoformat()))
            self.append(db, a)
        return a

    def view(self, case):
        workspace, payload = self.workspace(case)
        with self.base.connect() as db:
            events = self.latest(db, workspace)
        forecasts = self.forecasts(payload, events, reserved=True)
        recommendations = []
        supplier_requests = []
        ops = self.operations(payload, events, reserved=True)
        for target in forecasts:
            if not target.total_unmet:
                continue
            pack = next(i['units_per_case'] for i in payload['readiness']['identities']
                        if i['original']['source_organization'] == target.hospital_id)
            supplier_requests.append(ActionProposal(kind='replenishment', recipient=target.hospital_id,
                product_id=payload['product_id'], lot_id='sim-supplier-review-lot', quantity=target.total_unmet,
                units_per_case=pack, expected_on=date.fromisoformat(payload['anchor'])+timedelta(days=1),
                actor='operator-review-required').model_dump(mode='json'))
            for donor in forecasts:
                if donor.hospital_id == target.hospital_id or donor.total_unmet:
                    continue
                for lot in ops.inventory:
                    if lot.hospital_id != donor.hospital_id:
                        continue
                    quantity = min(lot.on_hand-lot.reserved, donor.points[-1].closing_usable, target.total_unmet)
                    if quantity <= 0:
                        continue
                    pack = next(i['units_per_case'] for i in payload['readiness']['identities']
                                if i['original']['source_organization'] == donor.hospital_id)
                    proposal = ActionProposal(kind='transfer', donor=donor.hospital_id, recipient=target.hospital_id,
                        product_id=payload['product_id'], lot_id=lot.lot_id, quantity=quantity, units_per_case=pack,
                        expected_on=date.fromisoformat(payload['anchor'])+timedelta(days=1), actor='operator-review-required')
                    try:
                        self.validate_transfer(proposal, quantity, payload, events)
                        recommendations.append(proposal.model_dump(mode='json'))
                    except ValueError:
                        pass
        return dict(schema_version='8.1', workspace=workspace, readiness=payload['readiness'],
            recommendations=recommendations,
            supplier_requests=supplier_requests,
            policy=POLICY, actions=[dict(a, next_allowed=TRANSITIONS.get(a['state'], []),
                documents=[dict(d, current_forecasts=[dict(hospital_id=f.hospital_id, total_unmet=f.total_unmet)
                    for f in self.forecasts(payload, events)]) for d in documents(a)]) for a in events],
            forecasts=[f.model_dump(mode='json') for f in self.forecasts(payload, events)],
            inventory=self.operations(payload, events).model_dump(mode='json')['inventory'],
            limitations=['Conservative forecast excludes unconfirmed inbound orders.',
                'Local role attestations are not authenticated; no real hospital or supplier access.',
                'Historical logical simulation clock; all dispatch/receiving records are synthetic.',
                'Different demand/receipt overlays on the same inventory export require explicit new reconciled snapshot.'])


def documents(action):
    p = action['proposal']
    common = dict(facility=p['recipient'], donor=p['donor'], product=p['product_id'], lot=p['lot_id'],
        quantity_bags=action['quantity_bags'], reason='Calculated shortage in fictional Helene network',
        evidence_ids=action['evidence_ids'], pending_approval=action['authorization'] is None,
        supporting_sources=action['supporting_sources'], baseline_forecasts=action['baseline_forecasts'],
        state=action['state'], next_allowed=TRANSITIONS.get(action['state'], []),
        unresolved=['Verify real stock, storage, partner policy and authorized identity before any real action.'],
        forecast_impact='Inspect current engine forecasts; request/authorization alone credits no stock.',
        classification='DRAFT / SIMULATED; not an external order, signature or shipment')
    return [dict(kind=kind, **common, received_bags=action['received'],
                 discrepancy_bags=action['dispatched']-action['received']) for kind in (
        'Interfacility transfer request' if p['kind'] == 'transfer' else 'Supplier replenishment inquiry',
        'Procurement exception / shortage escalation', 'Pharmacy review summary', 'Receiving reconciliation record')]
