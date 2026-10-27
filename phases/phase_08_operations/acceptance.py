"""Genuine loopback HTTP workflow, raw CSV mutation, H-MEM and safety acceptance."""
import argparse
import csv
from io import StringIO
import json
from pathlib import Path
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
import httpx
from phases.phase_05_product.service import OperatorService
from phases.phase_06_helene.pipeline import OPERATIONS
from .workflow import WorkflowStore
from .specialists import OperationalTools, remember_operations


def run(base, output):
    if urlsplit(base).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('LOOPBACK_ONLY_ACCEPTANCE')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=base, timeout=60) as client:
        listing = client.get('/api/v1/helene/cases').json()['items']
        item = next(c for c in listing if c['investigation_id'].startswith('helene-2024-10-01-') and not c.get('reconciliation_scenario'))
        case_id = item['investigation_id']
        path = '/api/v1/helene/cases/'+case_id+'/operations'
        before = client.get(path)
        before.raise_for_status()
        initial = before.json()
        assert initial['readiness']['supports_forecast']
        p = next(p for p in initial['recommendations'] if p['recipient'] == 'sim-nc-central')
        p.update(quantity=200, actor='acceptance-operator')
        def propose(_):
            return client.post(path+'/actions', json=p)
        with ThreadPoolExecutor(2) as pool:
            races = list(pool.map(propose, range(2)))
        assert sorted(r.status_code for r in races) == [201, 422]
        action = next(r.json() for r in races if r.status_code == 201)
        def advance(state, **extra):
            nonlocal action
            body = dict(expected_revision=action['revision'], state=state,
                actor='acceptance-pharmacist' if state == 'AUTHORIZED' else 'acceptance-operator',
                role='pharmacist' if state == 'AUTHORIZED' else 'receiving' if state == 'SIMULATED_RECEIPT' else 'operator',
                attested=True, note='Explicit synthetic acceptance; partial remainder under review')
            body.update(extra)
            response = client.post(path+'/actions/'+action['id']+'/transition', json=body)
            response.raise_for_status()
            action = response.json()
        denied = client.post(path+'/actions/'+action['id']+'/transition', json=dict(
            expected_revision=1, state='SIMULATED_DISPATCH', actor='acceptance-operator', role='operator', attested=True, note='must reject'))
        assert denied.status_code == 422
        for state in ('EVIDENCE_REVIEW', 'AUTHORIZATION_PENDING'):
            advance(state)
        advance('AUTHORIZED', policy_reference='fictional-pharmacy-policy-explicitly-reviewed')
        for state in ('REQUEST_PREPARED', 'SIMULATED_DISPATCH'):
            advance(state)
        advance('SIMULATED_RECEIPT', receipt_id='phase8-partial-receipt', received_quantity=120,
            lot_id=p['lot_id'], expires_on='2024-12-31', quality='released')
        duplicate = client.post(path+'/actions/'+action['id']+'/transition', json=dict(
            expected_revision=action['revision'], state='SIMULATED_RECEIPT', actor='acceptance-receiving', role='receiving',
            attested=True, note='duplicate must fail', receipt_id='phase8-partial-receipt', received_quantity=1,
            lot_id=p['lot_id'], expires_on='2024-12-31', quality='released'))
        assert duplicate.status_code == 422
        advance('CLOSED')
        final = client.get(path).json()
        assert next(f['total_unmet'] for f in initial['forecasts'] if f['hospital_id']=='sim-nc-central') - next(f['total_unmet'] for f in final['forecasts'] if f['hospital_id']=='sim-nc-central') == 120
        assert action['discrepancy_bags'] == 80
        assert sum(r['on_hand'] for r in initial['inventory']) - sum(r['on_hand'] for r in final['inventory']) == 80
        supplier = dict(p, kind='replenishment', donor=None, quantity=50, lot_id='synthetic-supplier-lot')
        supplier_result = client.post(path+'/actions', json=supplier)
        assert supplier_result.status_code == 201
        # Same public import API and forecast engine, new independent raw exports.
        folder = OPERATIONS / '2024-10-01'
        body = dict(checkpoint='2024-10-01', clocks=json.loads((folder/'clocks.json').read_text()),
            feeds={p.stem:p.read_text() for p in folder.glob('*.csv')})
        first = client.post('/api/v1/helene/operations/import', json=body)
        assert first.status_code in {200, 201}, first.text
        rows = list(csv.DictReader(body['feeds']['inventory'].splitlines()))
        rows[0]['on_hand'] = str(int(rows[0]['on_hand'])+1000)
        stream = StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        body['feeds']['inventory'] = stream.getvalue()
        second = client.post('/api/v1/helene/operations/import', json=body)
        assert second.status_code in {200, 201}, second.text
        raw_before = client.get('/api/v1/helene/cases/'+first.json()['investigation_id']).json()
        raw_after = client.get('/api/v1/helene/cases/'+second.json()['investigation_id']).json()
        assert raw_before['forecasts'][0]['total_unmet'] > raw_after['forecasts'][0]['total_unmet'] == 0
        (output/'operator-response.json').write_text(json.dumps(final, indent=2)+'\n')
        report = dict(http_base=base, case_id=case_id, concurrent_statuses=[r.status_code for r in races],
            missing_authorization_status=denied.status_code, duplicate_receipt_status=duplicate.status_code,
            action=action, initial_unmet=[f['total_unmet'] for f in initial['forecasts']],
            revised_unmet=[f['total_unmet'] for f in final['forecasts']],
            raw_import_mutation=dict(before_id=first.json()['investigation_id'], after_id=second.json()['investigation_id'],
                previous_unmet=raw_before['forecasts'][0]['total_unmet'], revised_unmet=raw_after['forecasts'][0]['total_unmet']))
    (output/'acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8878')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--store-root', type=Path, required=True)
    parser.add_argument('--resume-memory', action='store_true', help='Resume memory validation after an already retained HTTP run')
    args = parser.parse_args()
    report = (json.loads((args.output_dir/'acceptance.json').read_text()) if args.resume_memory
              else run(args.base_url, args.output_dir))
    service = OperatorService(args.store_root)
    case = service.helene_store.get(report['case_id'])
    workflow = WorkflowStore(service.store)
    view = workflow.view(case)
    tools = OperationalTools(case, workflow)
    for role, tool in [('dependency', 'identity.resolve'), ('hospital', 'readiness.inspect'), ('mitigation', 'actions.inspect')]:
        tools.call(role, tool)
    memory = remember_operations(args.store_root/'helene-memory', case, view)
    (args.output_dir/'specialist-memory.json').write_text(json.dumps(dict(tools=tools.receipts, memory=memory), indent=2)+'\n')
    print(json.dumps(dict(initial=report['initial_unmet'], revised=report['revised_unmet'],
        raw_mutation=report['raw_import_mutation'], memory_writes=len(memory['writes'])), indent=2))


if __name__ == '__main__':
    main()
