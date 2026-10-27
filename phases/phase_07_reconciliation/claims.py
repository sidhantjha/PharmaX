"""Normalize separately retained ERP records and manufacturer facts into claims."""
import csv
from datetime import datetime, time
from hashlib import sha256
from pathlib import Path
from contracts.models import SourceEvidence
from contracts.operational_claims import OperationalClaim
from contracts.replay import document_time
from phases.phase_06_helene.operational import bags


def case_claims(case, files):
    claims = []
    catalog = {r['facility_id']: r for r in files['catalog']}
    receipts = {r.feed: r for r in case.admission.imports}
    po_map = {p.purchase_order_id: p for p in case.operations.purchase_orders}
    for meaning, rows, key, feed in [
        ('requested', case.operations.purchase_orders, 'purchase_order_id', 'purchase_orders'),
        ('acknowledged', case.operations.acknowledgments, 'acknowledgment_id', 'acknowledgments'),
        ('shipped', case.operations.shipments, 'shipment_id', 'shipments'),
        ('usable_inventory', case.operations.inventory, 'inventory_id', 'inventory'),
    ]:
        for row in rows:
            cat = catalog[row.hospital_id]
            rid = getattr(row, key)
            po = row if meaning == 'requested' else po_map.get(getattr(row, 'purchase_order_id', None))
            raw = next((r for r in files[feed] if r['record_id'] == rid), {})
            clock = receipts[feed].modeled_available_at
            effective = datetime.fromisoformat(raw['snapshot_at']) if meaning == 'usable_inventory' else clock
            expected = getattr(row, 'expected_arrival', None)
            claims.append(OperationalClaim(claim_id=meaning+'-'+rid, source_id=feed,
                organization='Fictional hospital receiving' if meaning in {'requested', 'usable_inventory'} else
                'Fictional supplier', document_id=receipts[feed].source_path, record_id=rid,
                originating_claim_id=meaning+'-'+rid, observation='independent', meaning=meaning,
                classification='simulated', product_id=row.product_id, ndc='00338-0049-04',
                local_sku=cat['local_sku'], facility_id=row.hospital_id, account_id=cat['account_id'],
                order_id=po.purchase_order_id if po else None, line_id=po.order_line_id if po else None,
                shipment_id=getattr(row, 'shipment_id', None),
                quantity=row.on_hand-row.reserved if meaning == 'usable_inventory' else row.quantity,
                unit='bag', status=getattr(row, 'status', 'requested' if meaning == 'requested' else 'released'),
                scope='snapshot' if meaning == 'usable_inventory' else 'expectation' if meaning == 'requested'
                      else 'event_total', issued_at=effective, effective_at=effective, available_at=clock,
                expected_by=document_time(expected) if expected else None, verification='verified',
                evidence=row.evidence, reason='Normalized synthetic ERP record; verified means validated fixture '
                'identity/semantics, never a real-world authenticated transaction.'))
    for source in case.admission.sources:
        if str(source.issue_date) != '2024-09-29':
            continue  # Later allocation/recovery notices are not fresh closure observations.
        clock = document_time(source.issue_date)
        claims.append(OperationalClaim(claim_id=source.revision_id+'-production', source_id=source.revision_id,
            organization='Baxter', document_id=source.document_id, record_id=source.document_id,
            originating_claim_id='baxter-north-cove-disruption', observation='independent',
            meaning='production_status', classification='real', unit='status', status='disrupted', scope='policy',
            issued_at=clock, effective_at=clock, available_at=clock, verification='verified', evidence=source.evidence,
            reason='Retained authentic manufacturer document; historical availability is reconstructed, not proven.'))
    source_map = {s.revision_id: s for s in case.admission.sources}
    for notice in case.admission.allocations:
        source = source_map[notice.source_revision_id]
        clock = document_time(source.issue_date)
        claims.append(OperationalClaim(claim_id=notice.notice_id, source_id=source.revision_id,
            organization='Baxter', document_id=source.document_id, record_id=notice.notice_id,
            originating_claim_id=notice.notice_id, observation='independent', meaning='allocation_policy',
            classification='projected' if notice.kind == 'projected' else 'real', product_id=notice.product_id,
            ndc=notice.original_ndc, unit='percent', quantity=int(notice.allocation_percent), status='restricted',
            scope='policy', issued_at=clock, effective_at=datetime.combine(notice.effective_on, time.min, clock.tzinfo),
            available_at=clock, verification='verified', evidence=source.evidence,
            reason=f'Exact Baxter row {notice.original_baxter_code}, page {notice.page}; '
                   'allocation is account policy, not order fill or delivery.'))
    return claims


def receiving_claims(folder, case, files):
    path = Path(folder)/'receiving.csv'
    raw = path.read_bytes()
    hashed = sha256(raw).hexdigest()
    ev = SourceEvidence(evidence_id='sim-receiving-'+hashed, kind='simulated', source='Fictional hospital receiving',
        source_record_id='receiving.csv', source_url='https://example.invalid/pharmax/synthetic/receiving',
        observed_at=case.admission.imports[0].exported_at, raw_ref=str(path), content_sha256=hashed,
        note='Synthetic independently authored receiving record; inherited modeled export clock, '
             'not an attestation of physical receipt or actual document custody.')
    rows = list(csv.DictReader(raw.decode().splitlines()))
    if len(rows) > 100 or len({r['record_id'] for r in rows}) != len(rows):
        raise ValueError('RECEIVING_RECORD_BUDGET_OR_DUPLICATE')
    claims = []
    for r in rows:
        catalogs = [c for c in files['catalog'] if c['facility_id'] == r['facility_id'] and c['local_sku'] == r['local_sku']]
        if len(catalogs) != 1 or r['ndc'] != '00338-0049-04':
            raise ValueError('RECEIVING_PRODUCT_IDENTITY')
        po = next((p for p in case.operations.purchase_orders if p.purchase_order_id == r['po_id']), None)
        ship = next((s for s in case.operations.shipments if s.shipment_id == r['shipment_id']), None)
        if not po or not ship or ship.purchase_order_id != po.purchase_order_id or any([
            po.hospital_id != r['facility_id'], po.account_id != r['account_id'], po.order_line_id != r['line_id'],
            po.customer_item_id != r['local_sku'], ship.hospital_id != r['facility_id']]):
            raise ValueError('RECEIVING_ORDER_SHIPMENT_IDENTITY')
        if r['unit'] == 'case' and r['case_bags'] != catalogs[0]['case_bags']:
            raise ValueError('RECEIVING_PACK_MISMATCH')
        amount = bags(r['quantity'], r['unit'], catalogs[0])
        if amount > po.quantity or amount > ship.quantity:
            raise ValueError('RECEIVING_EXCEEDS_LINKED_SUPPLY')
        if r['quality'] not in {'released', 'quarantined'}:
            raise ValueError('RECEIVING_QUALITY_UNKNOWN')
        claims.append(OperationalClaim(claim_id=r['record_id'], source_id='hospital-receiving',
            organization='Fictional hospital receiving', document_id=str(path), record_id=r['record_id'],
            originating_claim_id=r['record_id'], observation='independent', meaning='accepted_receipt',
            classification='simulated', product_id=po.product_id, ndc=r['ndc'], local_sku=r['local_sku'],
            facility_id=r['facility_id'], account_id=r['account_id'], order_id=r['po_id'], line_id=r['line_id'],
            shipment_id=r['shipment_id'], quantity=amount, unit='bag', status=r['status'], scope=r['scope'],
            issued_at=r['effective_at'], effective_at=r['effective_at'], available_at=r['available_at'],
            verification='verified', evidence=ev, reason='Explicit synthetic hospital accepted receiving record.'))
    return claims, ev, rows
