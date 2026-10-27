"""Real HTTP acceptance plus raw receiving mutation into immutable child history."""
import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
import httpx
from phases.phase_05_product.service import OperatorService
from phases.phase_06_helene.fixtures import write_csv
from .fixtures import ROOT, DAY
from .pipeline import reconcile_scenario


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8877')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        health = client.get('/api/v1/health')
        health.raise_for_status()
        listing = client.get('/api/v1/helene/cases')
        listing.raise_for_status()
        scenarios = {}
        checkpoints = []
        for summary in listing.json()['items']:
            response = client.get('/api/v1/helene/cases/'+summary['investigation_id'])
            response.raise_for_status()
            case = response.json()
            v = client.get('/api/v1/helene/cases/'+case['investigation_id']+'/verification')
            v.raise_for_status()
            assert v.json()['schema_version'] == '7.1'
            if case.get('verification'):
                scenarios[case['verification']['scenario']] = case
            else:
                checkpoints.append(dict(id=case['investigation_id'], totals=[f['total_unmet'] for f in case['forecasts']],
                                        admitted_agencies=sorted({c['source_id'] for c in v.json()['claims'] if c['meaning']=='agency_guidance'})))
        assert set(scenarios) == {'A','B','C'}
        b = scenarios['B']
        assert b['verification']['physical_receipts'] == {'asn-central':500}
        assert b['forecasts'][0]['total_unmet'] == 176
        assert any(f['discrepancy_bags'] == 300 for f in b['verification']['findings'])
        assert all(not c['selected'] and c['verification']['requires_human_review'] for c in scenarios.values())
        report.update(http_base=args.base_url, health='passed', checkpoints=checkpoints,
            scenarios={s:dict(id=c['investigation_id'], unmet=[f['total_unmet'] for f in c['forecasts']],
                physical_receipts=c['verification']['physical_receipts'],
                missing=sum(f['category']=='missing' for f in c['verification']['findings']),
                conflicts=[f for f in c['verification']['findings'] if f['category']=='conflict'],
                actual_memory_writes=len(c['memory']['writes'])) for s,c in scenarios.items()})
        invalid = client.post('/api/v1/helene/reconciliation', json={'scenario':'D'})
        assert invalid.status_code == 422
    inputs = args.output_dir/'inputs'
    if inputs.exists():
        raise ValueError('ACCEPTANCE_REQUIRES_FRESH_OUTPUT_DIRECTORY')
    shutil.copytree(ROOT, inputs)
    path = inputs/'B'/DAY/'receiving.csv'
    before_hash = sha256(path.read_bytes()).hexdigest()
    parent = reconcile_scenario('B', root=inputs)
    before = parent.model_dump_json()
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[0]['quantity'] = '50'
    write_csv(path, rows)
    child = reconcile_scenario('B', root=inputs, parent=parent)
    assert parent.model_dump_json() == before
    store = OperatorService(args.output_dir/'store').helene_store
    store.save(parent)
    store.save(child)
    assert store.get(parent.investigation_id).model_dump_json() == before
    assert child.forecasts[0].total_unmet == 626
    report['raw_input_mutation'] = dict(before_hash=before_hash, after_hash=sha256(path.read_bytes()).hexdigest(),
        parent_id=parent.investigation_id, child_id=child.investigation_id, previous_unmet=176, revised_unmet=626,
        changes=child.verification.forecast_changes, immutable_parent='passed')
    (args.output_dir/'acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
