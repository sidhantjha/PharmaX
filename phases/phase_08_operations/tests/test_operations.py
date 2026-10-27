import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import shutil
import sqlite3
import pytest
from contracts.replay import document_time
from phases.phase_05_product.service import OperatorService
from phases.phase_06_helene.pipeline import replay_checkpoint, OPERATIONS
from phases.phase_06_helene.fixtures import write_csv
from phases.phase_08_operations.identity import ndc11, resolve, catalog_identity
from phases.phase_08_operations.ingestion import import_csv, inspect
from phases.phase_08_operations.workflow import WorkflowStore


@pytest.fixture
def setup(tmp_path):
    service = OperatorService(tmp_path)
    case = replay_checkpoint('2024-10-01')
    service.helene_store.save(case)
    return service, case, WorkflowStore(service.store)


def proposal(case, **updates):
    return dict(kind='transfer', recipient='sim-nc-central', donor='sim-nc-north',
        product_id=case.operations.products[0].product_id, lot_id='lot-north', quantity=100,
        units_per_case=12, expected_on='2024-10-03', actor='operator-one', **updates)


def transition(store, case, action, state, **extra):
    body = dict(expected_revision=action['revision'], state=state,
        actor='pharmacist-two' if state == 'AUTHORIZED' else 'operator-one',
        role='pharmacist' if state == 'AUTHORIZED' else 'receiving' if state == 'SIMULATED_RECEIPT' else 'operator',
        attested=True, note='Explicit fictional scenario review', **extra)
    return store.transition(case, action['id'], body)


def test_ndc_segment_rules_and_catalog_anchor():
    for value in ('0338-0049-04', '00338-0049-04', '00338004904'):
        assert ndc11(value) == '00338004904'
    assert ndc11('12345-678-90', '12345-678-90') == '12345067890'
    assert ndc11('12345-6789-0', '12345-6789-0') == '12345678900'
    for value in ('0338004904', '000338004904', '00338004905', 'abc-0049-04'):
        with pytest.raises(ValueError):
            ndc11(value)


def test_identity_preserves_source_and_rejects_similarity():
    rows = list(csv.DictReader((OPERATIONS / '2024-10-01/catalog.csv').read_text().splitlines()))
    results = [resolve(catalog_identity(r)) for r in rows]
    assert len({r['canonical_ndc'] for r in results}) == 1
    assert len({r['original']['local_sku'] for r in results}) == 3
    p = catalog_identity(rows[0]).model_dump()
    for changes in ({'volume_ml': 500}, {'strength_mg_ml': 3}, {'manufacturer_code': None, 'ndc': None},
                    {'container': 'bottle'}, {'ndc': '00338004905'}):
        assert resolve(dict(p, **changes))['review_required']


def test_readiness_import_and_negative_feeds(tmp_path):
    folder = tmp_path / '2024-10-01'
    shutil.copytree(OPERATIONS / '2024-10-01', folder)
    cutoff = document_time(date(2024, 10, 1))
    ops, receipts, report = import_csv(folder, cutoff)
    assert len(ops.hospitals) == 3 and len(receipts) == 9
    assert report['supports_forecast'] and len(report['identities']) == 3
    path = folder / 'inventory.csv'
    original = list(csv.DictReader(path.read_text().splitlines()))
    for change, expected in [({'snapshot_at': '2024-09-01T00:00:00-04:00'}, 'STALE'),
                             ({'on_hand': '-1'}, 'INVALID'), ({'unit': 'ml'}, 'UNSUPPORTED'),
                             ({'quality': ''}, 'MISSING')]:
        rows = [dict(r) for r in original]
        rows[0].update(change)
        write_csv(path, rows)
        result = inspect(folder, cutoff)[2]
        assert not result['supports_forecast'] and any(expected in e for e in result['errors'])
    rows = [dict(r) for r in original]
    rows += [dict(rows[0], record_id='expired', lot_id='expired', expires_on='2024-01-01'),
             dict(rows[0], record_id='recalled', lot_id='recalled', quality='recalled'),
             dict(rows[0], record_id='quarantine', lot_id='quarantine', quality='quarantined')]
    write_csv(path, rows)
    admitted, _, report = import_csv(folder, cutoff)
    assert len(admitted.inventory) == 3 and len(report['excluded_lots']) == 3
    write_csv(path, [dict(r, quality='quarantined') for r in original])
    admitted, _, report = import_csv(folder, cutoff)
    assert admitted.inventory == [] and len(report['excluded_lots']) == 3
    from phases.phase_06_helene.pipeline import forecast_operations
    assert all(f.initial_usable == 0 and f.total_unmet > 0 for f in
               forecast_operations(admitted, cutoff.date(), [], 'all-stock-quarantined'))
    write_csv(path, original + [dict(original[0], record_id='duplicate')])
    assert 'DUPLICATE_LOT_SNAPSHOT' in inspect(folder, cutoff)[2]['errors']
    write_csv(path, original + [dict(original[0], record_id='duplicate-alias', local_sku='alternate-alias')])
    assert 'DUPLICATE_LOT_SNAPSHOT' in inspect(folder, cutoff)[2]['errors']


def test_workflow_partial_receipt_persistence_and_audit(setup):
    service, case, store = setup
    before = store.view(case)
    a = store.propose(case, proposal(case))
    assert store.view(case)['forecasts'] == before['forecasts'] or [f['total_unmet'] for f in store.view(case)['forecasts']] == [f['total_unmet'] for f in before['forecasts']]
    with pytest.raises(ValueError, match='DISALLOWED'):
        transition(store, case, a, 'SIMULATED_DISPATCH')
    for state in ['EVIDENCE_REVIEW', 'AUTHORIZATION_PENDING']:
        a = transition(store, case, a, state)
    with pytest.raises(ValueError, match='POLICY'):
        transition(store, case, a, 'AUTHORIZED')
    a = transition(store, case, a, 'AUTHORIZED', policy_reference='sim-pharmacy-policy-reviewed')
    for state in ['REQUEST_PREPARED', 'SIMULATED_DISPATCH']:
        a = transition(store, case, a, state)
    a = transition(store, case, a, 'SIMULATED_RECEIPT', receipt_id='receipt-1', received_quantity=60,
        lot_id='lot-north', expires_on='2024-12-31', quality='released')
    with pytest.raises(ValueError, match='DUPLICATE_RECEIPT'):
        transition(store, case, a, 'SIMULATED_RECEIPT', receipt_id='receipt-1', received_quantity=1,
            lot_id='lot-north', expires_on='2024-12-31', quality='released')
    a = transition(store, case, a, 'CLOSED')
    assert a['discrepancy_bags'] == 40 and len(a['audit']) == a['revision']
    view = WorkflowStore(service.store).view(case)
    assert view['actions'][0]['state'] == 'CLOSED'
    assert view['forecasts'][0]['total_unmet'] == before['forecasts'][0]['total_unmet'] - 60
    assert sum(r['on_hand'] for r in view['inventory']) == sum(r['on_hand'] for r in before['inventory']) - 40
    assert service.helene_store.get(case.investigation_id) == case
    with service.store.connect() as db, pytest.raises(sqlite3.IntegrityError, match='immutable'):
        db.execute('DELETE FROM op_events')


def test_concurrent_reservations_and_cancellation(setup):
    _, case, store = setup
    def attempt(_):
        try:
            return store.propose(case, dict(proposal(case), quantity=200))
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    a = next(r for r in results if isinstance(r, dict))
    transition(store, case, a, 'CANCELLED')
    assert store.propose(case, proposal(case))['state'] == 'PROPOSED'


def test_unsafe_wrong_lot_pack_and_supplier(setup):
    _, case, store = setup
    for changes in ({'quantity': 500}, {'lot_id': 'not-a-lot'}, {'units_per_case': 14},
                    {'product_id': 'other-product'}):
        with pytest.raises(ValueError):
            store.propose(case, dict(proposal(case), **changes))
    p = dict(proposal(case), kind='replenishment', donor=None, lot_id='synthetic-supplier-lot')
    assert store.propose(case, p)['state'] == 'PROPOSED'


def test_operator_api_and_invalid_payloads(setup):
    service, case, _ = setup
    path = '/api/v1/helene/cases/' + case.investigation_id + '/operations'
    status, body = service.handle('GET', path)
    assert status == 200 and body['schema_version'] == '8.1'
    status, a = service.handle('POST', path + '/actions', proposal(case))
    assert status == 201
    assert service.handle('POST', path + '/actions/' + a['id'] + '/transition', {})[0] == 422


def test_same_csv_contract_imports_changed_raw_hospital_stock(setup):
    import json
    service, _, _ = setup
    folder = OPERATIONS / '2024-10-01'
    body = dict(checkpoint='2024-10-01', clocks=json.loads((folder/'clocks.json').read_text()),
        feeds={p.stem:p.read_text() for p in folder.glob('*.csv')})
    path = '/api/v1/helene/operations/import'
    status, result = service.handle('POST', path, body)
    assert status == 201, result
    parent = service.helene_store.get(result['investigation_id'])
    rows = list(csv.DictReader(body['feeds']['inventory'].splitlines()))
    rows[0]['on_hand'] = '1184'
    from io import StringIO
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    body['feeds']['inventory'] = stream.getvalue()
    status, result = service.handle('POST', path, body)
    assert status == 201, result
    child = service.helene_store.get(result['investigation_id'])
    assert parent.forecasts[0].total_unmet > child.forecasts[0].total_unmet == 0
    assert service.helene_store.get(parent.investigation_id) == parent
    body['clocks']['modeled_available_at'] = '2025-01-01T00:00:00-05:00'
    assert service.handle('POST', path, body)[0] == 422


def test_import_alias_variant_works_in_existing_verification_api(setup):
    import json
    service, _, _ = setup
    folder = OPERATIONS / '2024-10-01'
    body = dict(checkpoint='2024-10-01', clocks=json.loads((folder/'clocks.json').read_text()),
        feeds={p.stem:p.read_text() for p in folder.glob('*.csv')})
    body['feeds']['catalog'] = body['feeds']['catalog'].replace('00338-0049-04','0338-0049-04')
    status, result = service.handle('POST', '/api/v1/helene/operations/import', body)
    assert status == 201, result
    path = '/api/v1/helene/cases/'+result['investigation_id']
    assert service.handle('GET', path+'/verification')[0] == 200
    assert service.handle('GET', path+'/operations')[0] == 200


def test_future_missing_provenance_and_network_isolation(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.Client, 'send', lambda *args, **kwargs: pytest.fail('offline network'))
    folder = tmp_path/'ops'
    shutil.copytree(OPERATIONS/'2024-10-01', folder)
    cutoff = document_time(date(2024, 10, 1))
    assert import_csv(folder, cutoff)[2]['supports_forecast']
    (folder/'catalog.csv').unlink()
    assert not inspect(folder, cutoff)[2]['supports_forecast']


def test_strict_edi_subsets():
    from phases.phase_08_operations.edi import parse_subset
    refs = 'REF*PO*po-1~REF*LI*1~REF*AC*sim-account~REF*HF*sim-nc-central~REF*VN*IV-NS1L~'
    examples = [
        'ST*850*0001~BEG*00*NE*po-1**20241001~'+refs+'PO1*1*100*EA*0**VN*IV-NS1L*~CTT*1~SE*10*0001~',
        'ST*855*0001~BAK*00*AC*po-1*20241001~'+refs+'ACK*IA*100*EA~SE*9*0001~',
        'ST*856*0001~BSN*00*sim-asn*20241001*0000~'+refs+'SN1*1*100*EA~SE*9*0001~']
    for raw in examples:
        assert parse_subset(raw)['quantity'] == 100
        for invalid in (raw.replace('*EA', '*CA'), raw.replace('*100*', '*-1*'), raw.replace('SE*', 'BAD*')):
            with pytest.raises(ValueError):
                parse_subset(invalid)


def test_review_roles_wrong_expiry_and_revision(setup):
    _, case, store = setup
    a = store.propose(case, proposal(case))
    a = transition(store, case, a, 'EVIDENCE_REVIEW')
    with pytest.raises(ValueError, match='REVISION_CONFLICT'):
        store.transition(case, a['id'], dict(expected_revision=1, state='AUTHORIZATION_PENDING', actor='x', role='operator', attested=True, note='x'))
    a = transition(store, case, a, 'AUTHORIZATION_PENDING')
    with pytest.raises(ValueError, match='ROLE_NOT_AUTHORIZED'):
        store.transition(case, a['id'], dict(expected_revision=a['revision'], state='AUTHORIZED', actor='x', role='operator', attested=True, note='x', policy_reference='p'))
    with pytest.raises(ValueError, match='INDEPENDENT'):
        store.transition(case, a['id'], dict(expected_revision=a['revision'], state='AUTHORIZED', actor='operator-one', role='pharmacist', attested=True, note='x', policy_reference='p'))
    a = transition(store, case, a, 'AUTHORIZED', policy_reference='sim-reviewed-policy')
    a = transition(store, case, a, 'REQUEST_PREPARED')
    a = transition(store, case, a, 'SIMULATED_DISPATCH')
    for fields in ({'expires_on':'2025-01-01'}, {'lot_id':'wrong'}, {'received_quantity':101}):
        receipt = dict(receipt_id='invalid-receipt', received_quantity=100, lot_id='lot-north', expires_on='2024-12-31', quality='released')
        receipt.update(fields)
        with pytest.raises(ValueError):
            transition(store, case, a, 'SIMULATED_RECEIPT', **receipt)
    a = transition(store, case, a, 'SIMULATED_RECEIPT', receipt_id='quarantine', received_quantity=100,
        lot_id='lot-north', expires_on='2024-12-31', quality='quarantined')
    assert store.view(case)['forecasts'][0]['total_unmet'] == 256
