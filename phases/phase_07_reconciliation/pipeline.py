"""Phase 7 child revisions, supported receipts, conditional forecasts and feasibility."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from hashlib import sha256
from types import SimpleNamespace
from contracts.data import ReplayInventory
from contracts.models import SourceEvidence
from contracts.operational_claims import OperationalClaim
from phases.phase_01_foundation.sources import digest
from phases.phase_06_helene.operational import import_operations
from phases.phase_06_helene.pipeline import replay_checkpoint, forecast_operations, mitigate
from .claims import case_claims, receiving_claims
from .fixtures import ROOT, DAY, SCENARIOS
from .reconcile import reconcile, active_claims, quantity
from .sources import load_public


def verify_case(case, files=None, scenario='checkpoint'):
    if files is None:
        for receipt in case.admission.imports:
            if sha256(Path(receipt.source_path).read_bytes()).hexdigest() != receipt.content_sha256:
                raise ValueError('SAVED_INPUT_CHANGED_REIMPORT_REQUIRED')
        folder = Path(next(r.source_path for r in case.admission.imports if r.feed == 'inventory')).parent
        if any(r.mapping_version == 'nc-csv-v8.1' for r in case.admission.imports):
            from phases.phase_08_operations.ingestion import replay_importer
            _, _, files = replay_importer(folder, case.checkpoint.cutoff)
        else:
            _, _, files = import_operations(folder, case.checkpoint.cutoff)
    return reconcile(case_claims(case, files)+load_public(case.checkpoint.cutoff), case.checkpoint.cutoff, scenario)


def reconcile_scenario(scenario, *, root=ROOT, parent=None, memory_root=None, node=None):
    if scenario not in SCENARIOS:
        raise ValueError('UNKNOWN_RECONCILIATION_SCENARIO')
    folder = Path(root)/scenario/DAY
    base = replay_checkpoint(DAY, operations=Path(root)/scenario)
    _, _, files = import_operations(folder, base.checkpoint.cutoff)
    received, receipt_evidence, raw_receipts = receiving_claims(folder, base, files)
    claims = case_claims(base, files)+received+load_public(base.checkpoint.cutoff)
    result = reconcile(claims, base.checkpoint.cutoff, scenario)
    version = digest(dict(base=base.dataset_version, receiving=receipt_evidence.content_sha256,
                          claims=[c.model_dump(mode='json') for c in claims], policy='reconcile-v1',
                          parent_sha256=digest(parent.model_dump(mode='json')) if parent else None))
    ops = base.operations.model_copy(deep=True)
    active, _, _ = active_claims(result.claims, base.checkpoint.cutoff)
    # Incoming expectations never become physical stock. Credit only accepted post-snapshot receipts.
    credited = set()
    for ship_id, total in result.physical_receipts.items():
        records = [c for c in active if c.meaning == 'accepted_receipt' and c.shipment_id == ship_id]
        ship = next(s for s in ops.shipments if s.shipment_id == ship_id)
        if total > ship.quantity:
            raise ValueError('RECEIVING_AGGREGATE_EXCEEDS_SUPPLY')
        snapshot = datetime.fromisoformat(next(r['snapshot_at'] for r in files['inventory']
                                               if r['facility_id'] == ship.hospital_id))
        prior = [c for c in records if c.effective_at <= snapshot]
        prior_totals = [c for c in prior if c.scope == 'event_total']
        covered = quantity(prior_totals[0]) if prior_totals else sum(
            quantity(c) for c in {c.originating_claim_id: c for c in prior}.values())
        totals = [c for c in records if c.scope == 'event_total']
        records = totals[:1] if totals else list({c.originating_claim_id: c for c in records}.values())
        for record in records:
            row = next(r for r in raw_receipts if r['record_id'] == record.record_id)
            stock = next(r for r in files['inventory'] if r['facility_id'] == record.facility_id)
            if record.effective_at <= datetime.fromisoformat(stock['snapshot_at']):
                continue
            if date.fromisoformat(row['expires_on']) <= base.checkpoint.cutoff.date():
                raise ValueError('RECEIVING_EXPIRED_LOT')
            amount = max(0, quantity(record)-covered) if totals else quantity(record)
            if not amount:
                continue
            credited.add(ship_id)
            po = next(p for p in ops.purchase_orders if p.purchase_order_id == record.order_id)
            ops.inventory.append(ReplayInventory(inventory_id='received-'+record.claim_id,
                hospital_id=record.facility_id, product_id=record.product_id, supplier_id=po.supplier_id,
                lot_id='received-'+record.claim_id, as_of=base.checkpoint.cutoff.date(), on_hand=amount,
                reserved=0, expiry_date=row['expires_on'], quality_status=row['quality'], quantity_unit='bag',
                evidence=receipt_evidence))
    anchor = base.checkpoint.cutoff.date()
    forecasts = forecast_operations(ops, anchor, [], version)
    review = ['MATERIAL_UNRESOLVED_RECONCILIATION'] if result.requires_human_review else []
    permissions = {r['facility_id'] for r in files['suppliers'] if r['transfers_allowed'] == 'true'}
    mitigated, candidates, selected = mitigate(ops, anchor, [], version, forecasts, review, permissions)
    # Conditional scenario uses independently supplied ship quantity, never allocation * PO.
    conditional = []
    for ship in [c for c in active if c.meaning == 'shipped' and c.verification == 'verified']:
        outstanding = max(0, quantity(ship)-result.physical_receipts.get(ship.shipment_id, 0))
        if not outstanding:
            continue
        arrival = SimpleNamespace(hospital_id=ship.facility_id, quantity=outstanding,
            modeled_arrival=anchor+timedelta(days=1), shipment_id=ship.shipment_id, evidence_id=ship.evidence.evidence_id)
        alternative = forecast_operations(ops, anchor, [arrival], version)
        conditional.append(dict(shipment_id=ship.shipment_id, quantity=outstanding, quantity_unit='bag',
            assumption='Unconfirmed remainder arrives next day; sensitivity only, no mitigation authorization.',
            forecasts=[f.model_dump(mode='json') for f in alternative]))
        projected_evidence = SourceEvidence(evidence_id='conditional-'+digest(conditional[-1]), kind='derived',
            source='Shared inventory engine conditional supply sensitivity',
            source_url='https://example.invalid/pharmax/conditional/'+version, observed_at=base.created_at,
            content_sha256=digest(conditional[-1]), parent_ids=[ship.evidence.evidence_id],
            note='Calculated sensitivity over unconfirmed fictional incoming supply; never physical inventory.')
        result.claims.append(OperationalClaim(claim_id='conditional-'+ship.claim_id, source_id='forecast-engine',
            organization='PharmaX deterministic calculation', document_id=version, record_id=ship.shipment_id,
            originating_claim_id=ship.originating_claim_id, observation='derived', meaning='forecast_incoming',
            classification='projected', product_id=ship.product_id, ndc=ship.ndc, local_sku=ship.local_sku,
            facility_id=ship.facility_id, account_id=ship.account_id, order_id=ship.order_id, line_id=ship.line_id,
            shipment_id=ship.shipment_id, quantity=outstanding, unit='bag', status='conditional-only', scope='expectation',
            issued_at=base.checkpoint.cutoff, effective_at=base.checkpoint.cutoff+timedelta(days=1),
            available_at=base.checkpoint.cutoff, verification='unverified', evidence=projected_evidence,
            reason='What-if arrival tomorrow; not supplier confirmation or an observed receipt.'))
    result.conditional_forecasts = conditional
    previous = parent.forecasts if parent else base.forecasts
    result.forecast_changes = [dict(hospital_id=f.hospital_id, input_evidence_ids=[receipt_evidence.evidence_id],
        previous_initial_usable=p.initial_usable, revised_initial_usable=f.initial_usable,
        previous_unmet=p.total_unmet, revised_unmet=f.total_unmet, delta=f.total_unmet-p.total_unmet,
        previous_analysis_id=p.analysis_id, revised_analysis_id=f.analysis_id)
        for f in forecasts for p in previous if p.hospital_id == f.hospital_id]
    # Keep every source and the exact raw receipt hash in the immutable child.
    evidence = [e for e in base.evidence if e.kind != 'derived']+[receipt_evidence]
    for c in result.claims:
        if c.evidence.evidence_id not in {e.evidence_id for e in evidence}:
            evidence.append(c.evidence)
    evidence.append(SourceEvidence(evidence_id='replay-dataset-'+version, kind='derived',
        source='Phase 7 reconciliation calculation', source_url='https://example.invalid/pharmax/reconciliation/'+version,
        observed_at=datetime.now(timezone.utc), content_sha256=version,
        parent_ids=[e.evidence_id for e in evidence], note='Calculated over synthetic operations and observed documents.'))
    cp = base.checkpoint.model_copy(update=dict(checkpoint_id='helene-p7-'+scenario+'-'+version[:12],
        parent_checkpoint_id=parent.checkpoint.checkpoint_id if parent else None,
        parent_cutoff=parent.checkpoint.cutoff if parent else None,
        parent_sha256=digest(parent.model_dump(mode='json')) if parent else None))
    memory = {}
    if memory_root is not None:
        from .specialists import remember_verification
        memory = remember_verification(memory_root, result, version, node=node,
                                      prior_scope=parent.memory.get('scope') if parent else None)
    decisions = [dict(agent_id=role, tool=tool, execution='local-deterministic', result=values)
        for role, tool, values in [
            ('verification', 'evidence.reconcile', dict(findings=len(result.findings),
                evidence_ids=[e.evidence_id for e in evidence], provider_inference=False)),
            ('dependency', 'orders.implicated', dict(order_ids=sorted({c.order_id for c in claims if c.order_id}))),
            ('hospital', 'inventory.forecast', dict(analysis_ids=[f.analysis_id for f in forecasts])),
            ('mitigation', 'mitigation.evaluate', dict(selected=selected, candidates=len(candidates),
                                                    requires_human_review=result.requires_human_review)),
        ]]
    if memory:
        decisions.append(dict(agent_id='memory', tool='hmem.exchange', execution='actual-node-reference', result=memory))
    # No action draft is approvable against material unresolved supply evidence.
    admission = base.admission.model_copy(update={'checkpoint': cp})
    child = base.model_copy(update=dict(investigation_id='helene-'+DAY+'-p7-'+scenario+'-'+version[:12],
        checkpoint=cp, admission=admission, dataset_version=version, operations=ops,
        parent_id=parent.investigation_id if parent else None, forecasts=forecasts, mitigated=mitigated,
        candidates=candidates, selected=selected, actions=[], evidence=evidence, verification=result,
        unresolved=review, decisions=decisions, memory=memory,
        reconciliation=[dict(policy='reconcile-v1', receiving_sha256=receipt_evidence.content_sha256,
            credited_shipment_ids=sorted(credited), changes=result.forecast_changes,
            previous_selected=parent.selected if parent else base.selected,
            reason='All mitigation candidates recomputed against accepted supply; conditional arrivals excluded.')]))
    return type(base).model_validate(child.model_dump(mode='python'))
