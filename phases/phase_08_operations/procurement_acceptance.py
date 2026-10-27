"""Actual loopback supplier-inquiry lifecycle; all confirmations are synthetic."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5188')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if urlsplit(args.base_url).hostname not in {'localhost', '127.0.0.1'}:
        raise ValueError('LOOPBACK_ONLY_ACCEPTANCE')
    with httpx.Client(base_url=args.base_url, timeout=60, trust_env=False) as client:
        cases = client.get('/api/v1/helene/cases').json()['items']
        case = next(c for c in cases if c['investigation_id'].startswith('helene-2024-10-01-')
                    and not c.get('reconciliation_scenario') and c['parent_id'])
        path = '/api/v1/helene/cases/'+case['investigation_id']+'/operations'
        before = client.get(path).json()
        request = next(p for p in before['supplier_requests'] if p['recipient']=='sim-nc-central')
        request.update(quantity=50, lot_id='sim-supplier-acceptance-lot', actor='procurement-operator')
        response = client.post(path+'/actions', json=request)
        response.raise_for_status()
        a = response.json()
        for state in ['EVIDENCE_REVIEW', 'AUTHORIZATION_PENDING', 'AUTHORIZED', 'REQUEST_PREPARED',
                      'SIMULATED_DISPATCH', 'SIMULATED_RECEIPT', 'CLOSED']:
            body = dict(expected_revision=a['revision'], state=state,
                actor='procurement-pharmacist' if state=='AUTHORIZED' else 'procurement-operator',
                role='pharmacist' if state=='AUTHORIZED' else 'receiving' if state=='SIMULATED_RECEIPT' else 'operator',
                attested=True, note='Explicit synthetic supplier review; 20 bags missing, escalation retained')
            if state=='AUTHORIZED':
                body['policy_reference']='simulated-procurement-policy-review'
            if state=='SIMULATED_RECEIPT':
                body.update(receipt_id='sim-procurement-partial', received_quantity=30,
                    lot_id=request['lot_id'], expires_on='2025-01-01', quality='released')
            response = client.post(path+'/actions/'+a['id']+'/transition', json=body)
            response.raise_for_status()
            a = response.json()
        after = client.get(path).json()
        delta = before['forecasts'][0]['total_unmet'] - after['forecasts'][0]['total_unmet']
        assert delta == 30 and a['discrepancy_bags'] == 20
        assert a['dispatch_record']['classification']=='simulated; no external confirmation'
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(dict(classification='actual HTTP over synthetic supplier workflow',
            action=a, previous_unmet=before['forecasts'][0]['total_unmet'],
            revised_unmet=after['forecasts'][0]['total_unmet'],
            documents=next(v['documents'] for v in after['actions'] if v['id']==a['id'])), indent=2)+'\n')
        print(json.dumps(dict(state=a['state'], received=a['received'], discrepancy=a['discrepancy_bags'], forecast_delta=delta)))


if __name__ == '__main__':
    main()
