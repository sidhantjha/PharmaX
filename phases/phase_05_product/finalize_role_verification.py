"""Local-only finalization of a stopped real run. This module cannot infer."""
import argparse
from datetime import datetime,timezone
import json
from phases.phase_04_agents.models import Finding
from phases.phase_06_helene.specialists import remember
from .integration import RESPONSIBILITIES
from .service import OperatorService
from .verified_runtime import MODEL


def main():
    from pathlib import Path
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--store-root',type=Path,required=True)
    args=p.parse_args()
    root=args.run_dir
    summary=json.loads((root/'summary.json').read_text())
    if summary.get('finalized_at'):
        raise ValueError('RUN_ALREADY_FINALIZED_PRESERVE_RECEIPTS')
    service=OperatorService(args.store_root)
    case=service.helene_store.get(summary['investigation_id'])
    registry={e.evidence_id:e for e in case.evidence}
    records={r:json.loads((root/(r+'.json')).read_text()) for r in RESPONSIBILITIES}
    starts=[t['started_at'] for r in records.values() for t in r.get('request_records',[])]
    if not starts or (datetime.now(timezone.utc)-datetime.fromisoformat(min(starts))).total_seconds()<240:
        raise ValueError('DEADLINE_CLOSURE_NOT_ESTABLISHED')
    accepted=[]
    for role,record in records.items():
        if record['status']=='complete':
            answer=record['structured_finding']
            accepted.append(Finding(claim_key='live-'+role,value=json.dumps(answer),kind='derived',
                evidence_ids=answer['evidence_ids'],source_ids=sorted({registry[i].source for i in answer['evidence_ids']}),
                limitations=['Genuine current model interpretation of reconstructed historical/synthetic case; no historical inference availability claim.']))
    memory=remember(root/'hmem','live-verification-final',case.dataset_version,case.checkpoint.cutoff,[],[],
        node=service.node,additional_findings=accepted,query_text='source custody simulated stock unavailable trade review')
    (root/'hmem.json').write_text(json.dumps(memory,indent=2)+'\n')
    smoke=json.loads((root/'smoke.json').read_text())['model_receipt']
    receipts=[smoke]+[m for r in records.values() for m in r.get('model_receipts',[])]
    if any(m['returned_model']!=MODEL or m['winning_route']['provider']!='nvidia' for m in receipts):
        raise ValueError('RECEIPT_ROUTE_MISMATCH')
    if len({m['response_id'] for m in receipts})!=len(receipts):
        raise ValueError('DUPLICATE_MODEL_RECEIPT')
    original=json.loads((root/'trace.json').read_text())
    # Retain the exact compact tool projections actually returned to each model,
    # separately from the underlying full canonical reads and their hashes.
    for e in original['events']:
        if e['operation']=='data.specialist_read':
            r=e['data']['agent_id']
            (root/(r+'-model-tool-result.json')).write_text(json.dumps(e['data']['result'],indent=2)+'\n')
    (root/'before-finalization-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    summary.update(status='partial',error_code='VERIFICATION_TIME_BUDGET_EXHAUSTED',
        finalized_at=datetime.now(timezone.utc).isoformat(),further_inference_authorized=False,
        genuinely_live_roles=sum(bool(r.get('model_receipts')) for r in records.values()),
        specialists_completed=sum(r['status']=='complete' for r in records.values()),
        actual_model_turns_including_smoke=len(receipts),
        total_input_tokens=sum(m['tokens'].get('prompt_tokens',0) for m in receipts),
        total_output_tokens=sum(m['tokens'].get('completion_tokens',0) for m in receipts),
        possible_upstream_attempt_exposure=3*len(receipts),actual_upstream_attempts=None,
        roles={r:v['status'] for r,v in records.items()},
        hmem=dict(actual=True,writes=len(memory['writes']),
            recalled=len(memory.get('result',{}).get('memories',[])),model_role_attempted=False),
        validation_note='Dependency finding accepted only after checking unchanged native output against actual predecessor evidence; original rejection preserved. Hospital off-scope finding remains failed. No model call repeated.',
        cost=dict(confirmed_route='NVIDIA Build free prototyping',cost_usd=0,billing_receipt_available=False,
            basis='Official NVIDIA no-cost FAQ and rate-limit clarification plus explicit active-free-key user authorization; fixed-origin source guard and matching actual NVIDIA receipts.'))
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('status','genuinely_live_roles','specialists_completed',
        'actual_model_turns_including_smoke','total_input_tokens','total_output_tokens',
        'possible_upstream_attempt_exposure','actual_upstream_attempts','hmem','error_code')},indent=2))


if __name__=='__main__':
    main()
