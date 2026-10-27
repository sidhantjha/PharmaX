import csv
from datetime import date, timedelta
import json
import shutil
import sqlite3
import httpx
import pytest
from contracts.operational_claims import OperationalClaim
from contracts.replay import document_time
from phases.phase_01_foundation.sources import digest
from phases.phase_05_product.service import OperatorService
from phases.phase_06_helene.fixtures import write_csv
from phases.phase_06_helene.pipeline import replay_checkpoint
from phases.phase_07_reconciliation.fixtures import ROOT, DAY, generate
from phases.phase_07_reconciliation.pipeline import reconcile_scenario, verify_case
from phases.phase_07_reconciliation.reconcile import reconcile
from phases.phase_07_reconciliation.sources import load_public, ROOT as PUBLIC
from phases.phase_07_reconciliation.specialists import ReconciliationTools


@pytest.fixture(scope='module')
def cases():
    return {s: reconcile_scenario(s) for s in ('A', 'B', 'C')}


def change(claim, **updates):
    return OperationalClaim.model_validate(dict(claim.model_dump(mode='python'), **updates))


def test_offline_scenarios_and_real_calculated_outputs(cases, monkeypatch):
    monkeypatch.setattr(httpx.Client, 'send', lambda *a, **k: pytest.fail('offline network'))
    b = reconcile_scenario('B')
    assert b.verification.physical_receipts == {'asn-central': 500}
    assert [(f.initial_usable, f.total_unmet) for f in b.forecasts] == [(664, 176), (619, 0), (110, 170)]
    assert b.verification.requires_human_review and not b.selected and not b.actions
    assert all(not c['feasible'] for c in b.candidates)
    assert any('UNRESOLVED_SUPPLY_EVIDENCE' in c['failures'] for c in b.candidates)
    for case in cases.values():
        for f in case.forecasts:
            for p in f.points:
                assert p.closing_physical == p.opening_physical-p.expired_physical+p.received-p.served
    assert any(f.discrepancy_bags == 300 and f.category == 'conflict' for f in b.verification.findings)
    remainder = next(c for c in b.verification.conditional_forecasts if c['shipment_id'] == 'asn-central')
    assert remainder['quantity'] == 300
    assert remainder['forecasts'][0]['total_unmet'] == 0


def test_allocation_is_compatible_with_request_and_missing_ack_unknown(cases):
    a = cases['A'].verification
    assert next(c for c in a.claims if c.meaning == 'requested' and c.facility_id == 'sim-nc-central').quantity == 1000
    assert next(c for c in a.claims if c.meaning == 'allocation_policy').quantity == 40
    assert any(f.category == 'compatible' and 'percentage cannot' in f.reason for f in a.findings)
    assert not any(f.category == 'conflict' for f in a.findings)
    assert a.physical_receipts == {}
    assert not any(c['quantity'] == 400 for c in a.conditional_forecasts)
    c = cases['C'].verification
    assert any(f.category == 'missing' and 'acknowledgment' in f.reason for f in c.findings)
    assert c.requires_human_review and not cases['C'].selected


def test_repeated_manufacturer_statement_never_independent_shipment_confirmation():
    cutoff = document_time(date(2024, 10, 12))
    claims = load_public(cutoff)
    repeated = [c for c in claims if c.meaning == 'production_status']
    assert len(repeated) == 3 and len({c.originating_claim_id for c in repeated}) == 1
    v = reconcile(repeated, cutoff)
    assert all(f.category == 'compatible' and f.independent_origins == 1 for f in v.findings)
    assert v.physical_receipts == {}
    assert all(c.meaning not in {'shipped', 'accepted_receipt'} for c in claims)


@pytest.mark.parametrize('day,ids', [('2024-10-01', set()), ('2024-10-04', {'sc-dph'}),
    ('2024-10-08', {'sc-dph', 'va-doh'}), ('2024-10-12', {'sc-dph', 'va-doh', 'cdc-han-00518'})])
def test_public_historical_admission_and_revised_page_limit(day, ids):
    claims = load_public(document_time(date.fromisoformat(day)))
    assert {c.source_id for c in claims} == ids
    if 'va-doh' in ids:
        va = next(c for c in claims if c.source_id == 'va-doh')
        assert va.evidence.updated_on == date(2026, 1, 21)
        assert va.evidence.version_available_at is None
    if ids:
        with pytest.raises(ValueError, match='CUSTODY_UNPROVEN'):
            load_public(document_time(date.fromisoformat(day)), strict=True)


def test_same_fulfillment_event_totals_conflict_and_no_receipt_credit(cases):
    receipt = next(c for c in cases['B'].verification.claims if c.meaning == 'accepted_receipt')
    other = change(receipt, claim_id='other-receipt', originating_claim_id='other-origin', quantity=600)
    v = reconcile([receipt, other], cases['B'].checkpoint.cutoff)
    assert any(f.category == 'conflict' and f.discrepancy_bags == 100 for f in v.findings)
    assert not v.physical_receipts


@pytest.mark.parametrize('update', [dict(facility_id='sim-nc-south'), dict(shipment_id='other'),
    dict(ndc='00338-0049-05'), dict(account_id='other'), dict(line_id='other')])
def test_mismatched_events_do_not_create_false_conflict(cases, update):
    r = next(c for c in cases['B'].verification.claims if c.meaning == 'accepted_receipt')
    other = change(r, claim_id='different', originating_claim_id='different', quantity=600, **update)
    assert not any(f.category == 'conflict' for f in reconcile([r, other], cases['B'].checkpoint.cutoff).findings)


def test_partial_and_open_window_no_false_contradiction(cases):
    r = next(c for c in cases['B'].verification.claims if c.meaning == 'accepted_receipt')
    ship = next(c for c in cases['B'].verification.claims if c.meaning == 'shipped' and c.shipment_id == r.shipment_id)
    partial = change(r, scope='partial', status='accepted')
    v = reconcile([ship, partial], cases['B'].checkpoint.cutoff)
    assert not any(f.category == 'conflict' for f in v.findings)
    assert v.physical_receipts[ship.shipment_id] == 500
    future_window = change(ship, expected_by=cases['B'].checkpoint.cutoff+timedelta(days=1))
    assert not any(f.category == 'conflict' for f in reconcile([future_window, r], cases['B'].checkpoint.cutoff).findings)


def test_partials_sum_and_cumulative_total_deduplicates(cases):
    r = next(c for c in cases['B'].verification.claims if c.meaning == 'accepted_receipt')
    a = change(r, scope='partial', status='accepted', quantity=200)
    b = change(a, claim_id='partial-two', originating_claim_id='partial-two', quantity=300)
    copy = change(a, claim_id='repeat-partial', observation='repeated')
    cutoff = cases['B'].checkpoint.cutoff
    assert reconcile([a, b, copy], cutoff).physical_receipts == {'asn-central': 500}
    total = change(r, claim_id='cumulative')
    assert reconcile([a, b, total], cutoff).physical_receipts == {'asn-central': 500}


def test_explicit_supersession_and_newer_alone_does_not_win(cases):
    r = next(c for c in cases['B'].verification.claims if c.meaning == 'accepted_receipt')
    later = change(r, claim_id='corrected', effective_at=r.effective_at+timedelta(minutes=10), quantity=600)
    cutoff = cases['B'].checkpoint.cutoff
    v = reconcile([r, later], cutoff)
    assert not v.physical_receipts and any(f.category == 'missing' for f in v.findings)
    revised = change(later, supersedes=r.claim_id)
    v = reconcile([r, revised], cutoff)
    assert v.physical_receipts == {'asn-central': 600}
    assert any(f.category == 'superseded' for f in v.findings) and len(v.claims) == 2
    with pytest.raises(ValueError, match='SUPERSESSION'):
        reconcile([r, change(revised, originating_claim_id='other')], cutoff)


def test_unit_conversion_and_validation(cases):
    r = next(c for c in cases['B'].verification.claims if c.meaning == 'accepted_receipt')
    case_units = change(r, unit='case', quantity=50, case_bags=10)
    copy = change(r, claim_id='bag-report', originating_claim_id='bag-report')
    v = reconcile([case_units, copy], cases['B'].checkpoint.cutoff)
    assert v.physical_receipts == {'asn-central': 500}
    assert any(f.category == 'consistent' for f in v.findings)
    with pytest.raises(ValueError):
        change(r, unit='case')
    with pytest.raises(ValueError):
        change(r, ndc=None)


def test_stale_unverified_and_future_records_stay_unknown(cases):
    c = cases['B']
    r = next(v for v in c.verification.claims if v.meaning == 'accepted_receipt')
    assert not reconcile([change(r, verification='unverified')], c.checkpoint.cutoff).physical_receipts
    v = reconcile([change(r, available_at=c.checkpoint.cutoff+timedelta(days=1))], c.checkpoint.cutoff)
    assert not v.physical_receipts and v.excluded_claim_ids == [r.claim_id]
    inventory = next(v for v in c.verification.claims if v.meaning == 'usable_inventory')
    stale = change(inventory, effective_at=c.checkpoint.cutoff-timedelta(days=2))
    assert reconcile([stale], c.checkpoint.cutoff).requires_human_review


def test_raw_receiving_mutation_changes_engine_and_immutable_history(tmp_path, cases):
    root = tmp_path/'inputs'
    shutil.copytree(ROOT, root)
    base = reconcile_scenario('B', root=root)
    original = base.model_dump_json()
    path = root/'B'/DAY/'receiving.csv'
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[0]['quantity'] = '50'
    write_csv(path, rows)
    child = reconcile_scenario('B', root=root, parent=base)
    assert child.forecasts[0].total_unmet == 626
    assert base.forecasts[0].total_unmet == 176 and base.model_dump_json() == original
    assert child.verification.forecast_changes[0]['delta'] == 450
    assert child.checkpoint.parent_sha256 == digest(base.model_dump(mode='json'))
    service = OperatorService(tmp_path/'store')
    service.helene_store.save(base)
    service.helene_store.save(child)
    assert service.helene_store.get(base.investigation_id).model_dump_json() == original
    with service.store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM helene_cases')
    assert child.evidence[-1].parent_ids and child.dataset_version != base.dataset_version


@pytest.mark.parametrize('field,value,code', [('ndc','99999-9999-99','PRODUCT'),
    ('shipment_id','wrong','IDENTITY'), ('account_id','wrong','IDENTITY'),
    ('local_sku','wrong','PRODUCT'), ('unit','bottle','UNIT'), ('quantity','1001','EXCEEDS')])
def test_raw_identity_and_unit_fail_closed(tmp_path, field, value, code):
    shutil.copytree(ROOT, tmp_path/'inputs')
    path = tmp_path/'inputs'/'B'/DAY/'receiving.csv'
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[0][field] = value
    write_csv(path, rows)
    with pytest.raises(ValueError, match=code):
        reconcile_scenario('B', root=tmp_path/'inputs')


def test_receipt_before_snapshot_not_double_credited_and_quarantine_excluded(tmp_path):
    shutil.copytree(ROOT, tmp_path/'inputs')
    path = tmp_path/'inputs'/'B'/DAY/'receiving.csv'
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[0]['effective_at'] = '2024-10-01T22:30:00-04:00'
    write_csv(path, rows)
    assert reconcile_scenario('B', root=tmp_path/'inputs').forecasts[0].initial_usable == 164
    rows[0]['effective_at'] = '2024-10-01T23:30:00-04:00'
    rows[0]['quality'] = 'quarantined'
    write_csv(path, rows)
    assert reconcile_scenario('B', root=tmp_path/'inputs').forecasts[0].initial_usable == 164


def test_specialist_roles_real_tools_and_budgets(cases):
    t = ReconciliationTools(cases['B'])
    assert t.call('verification', 'evidence.records')
    assert t.call('verification', 'evidence.findings')
    assert 'po-central' in t.call('dependency', 'orders.implicated')
    assert t.call('mitigation', 'supply.validated')['receipts']['asn-central'] == 500
    with pytest.raises(ValueError, match='DENIED'):
        t.call('mitigation', 'evidence.records')
    t.calls = 12
    with pytest.raises(ValueError, match='BUDGET'):
        t.call('verification', 'evidence.records')


def test_http_routes_and_real_hmem_recall(tmp_path):
    service = OperatorService(tmp_path/'api')
    status, result = service.handle('POST', '/api/v1/helene/reconciliation', {'scenario':'B'})
    assert status == 201, result
    first_id = result['investigation_id']
    repeated_status, repeated = service.handle('POST', '/api/v1/helene/reconciliation', {'scenario':'B'})
    assert repeated_status == 200 and repeated['investigation_id'] == first_id
    assert result['memory']['writes']
    status, v = service.handle('GET', f'/api/v1/helene/cases/{first_id}/verification')
    assert status == 200 and v['physical_receipts'] == {'asn-central':500}
    status, child = service.handle('POST', '/api/v1/helene/reconciliation', {'scenario':'A', 'parent_id':first_id})
    assert status == 201 and child['memory']['prior_scope'] == result['memory']['scope']
    assert child['memory']['prior_rejections']
    assert service.handle('POST', '/api/v1/helene/reconciliation', {'scenario':'D'})[0] == 422
    assert service.handle('POST', '/api/v1/helene/reconciliation', {'scenario':'A','root':'/tmp'})[0] == 422
    assert service.handle('GET', f'/api/v1/helene/cases/{first_id}')[1]['verification'] == v


def test_existing_checkpoint_verification_and_public_hash_tamper(tmp_path):
    c = replay_checkpoint('2024-10-09')
    result = verify_case(c)
    assert {v.source_id for v in result.claims if v.meaning == 'agency_guidance'} == {'sc-dph','va-doh'}
    shutil.copytree(PUBLIC, tmp_path/'public')
    manifest = json.loads((tmp_path/'public'/'manifest.json').read_text())
    (tmp_path/'public'/manifest[0]['file']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        load_public(c.checkpoint.cutoff, root=tmp_path/'public')


def test_fixture_generation_reproduces_bytes(tmp_path):
    generate(tmp_path/'generated')
    for path in ROOT.rglob('*'):
        if path.is_file():
            assert path.read_bytes() == (tmp_path/'generated'/path.relative_to(ROOT)).read_bytes()


def test_cumulative_receipt_subtracts_pre_snapshot_partial_and_aggregate_overage_fails(tmp_path):
    shutil.copytree(ROOT, tmp_path/'inputs')
    path = tmp_path/'inputs'/'B'/DAY/'receiving.csv'
    rows = list(csv.DictReader(path.read_text().splitlines()))
    old = dict(rows[0], record_id='early-partial', quantity='200', scope='partial', status='accepted',
               effective_at='2024-10-01T22:30:00-04:00')
    write_csv(path, [old, *rows])
    c = reconcile_scenario('B', root=tmp_path/'inputs')
    assert c.verification.physical_receipts['asn-central'] == 500
    assert c.forecasts[0].initial_usable == 464  # 164 snapshot + (500 total - 200 already covered)
    rows[0].update(scope='partial', status='accepted', quantity='700')
    write_csv(path, [old, *rows])
    with pytest.raises(ValueError, match='AGGREGATE_EXCEEDS'):
        reconcile_scenario('B', root=tmp_path/'inputs')


def test_repeated_receiving_without_original_is_unknown(cases):
    c = cases['B']
    receipt = next(r for r in c.verification.claims if r.meaning == 'accepted_receipt')
    v = reconcile([change(receipt, observation='repeated')], c.checkpoint.cutoff)
    assert v.physical_receipts == {} and v.requires_human_review


def test_saved_case_read_rejects_mutated_source(tmp_path):
    shutil.copytree(ROOT, tmp_path/'inputs')
    case = replay_checkpoint(DAY, operations=tmp_path/'inputs'/'B')
    path = tmp_path/'inputs'/'B'/DAY/'purchase_orders.csv'
    path.write_text(path.read_text().replace('1000', '1001'))
    with pytest.raises(ValueError, match='REIMPORT_REQUIRED'):
        verify_case(case)
