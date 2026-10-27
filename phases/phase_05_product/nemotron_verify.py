"""Explicit one-smoke / twelve-role verification command; no production approval."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
from uuid import uuid4
import httpx
from pydantic import Field
from contracts.models import Contract
from phases.phase_01_foundation.http import ServiceError
from .execution import ExecutionTrace
from .live_specialists import ModelSession
from .verification_budget import VerificationBudget
from .verified_runtime import reviewed_authorization, MODEL


class SmokeAck(Contract):
    status: str = Field(pattern='^ok$')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--base-url',required=True)
    p.add_argument('--runtime-token-file',type=Path,required=True)
    p.add_argument('--engine-root',type=Path,required=True)
    p.add_argument('--state-root',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--store-root',type=Path)
    p.add_argument('--smoke-only',action='store_true')
    p.add_argument('--smoke-receipt',type=Path,help='Reuse the already verified smoke; never dispatch another smoke')
    p.add_argument('--resume',action='store_true',help='Resume remaining roles of this same investigation, retaining consumed budgets')
    args=p.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    if (args.output_dir/'smoke.json').exists() and not args.resume:
        raise ValueError('USE_FRESH_OUTPUT_DIRECTORY_NO_AUTOMATIC_RETRY')
    os.environ['METERLESS_BASE_URL']=args.base_url
    os.environ['METERLESS_MODEL']=MODEL
    os.environ['METERLESS_RUNTIME_TOKEN']=args.runtime_token_file.read_text().strip()
    budget=VerificationBudget(max_turns=1 if args.smoke_only else 25,
                              max_attempts=3 if args.smoke_only else 75)
    approval=reviewed_authorization(args.base_url,args.engine_root,args.state_root,budget)
    trace=ExecutionTrace()
    receipt=dict(classification='actual live attempt; inspect status',requested_model=MODEL,
        started_at=datetime.now(timezone.utc).isoformat(),live_verified=False)
    with httpx.Client(timeout=httpx.Timeout(30,connect=3),trust_env=False,follow_redirects=False) as client:
        if args.smoke_receipt:
            prior=json.loads(args.smoke_receipt.read_text())
            if not prior.get('live_verified') or prior.get('requested_model')!=MODEL or prior.get('status')!='passed':
                raise ValueError('SMOKE_RECEIPT_NOT_VERIFIED')
            budget.turns=1
            budget.attempts=3
            budget.roles['smoke']=1
            budget.reported_tokens=prior['model_receipt']['tokens']['total_tokens']
            (args.output_dir/'smoke.json').write_text(json.dumps(prior,indent=2)+'\n')
            from .helene_model_verification import run_roles
            return run_roles(args,client,approval,budget)
        session=ModelSession(client,authorization=approval,max_requests=1,
            profiles={'smoke':'Call smoke_ack with status ok. No other task.'},budget=budget,max_tokens=64)
        try:
            if MODEL not in session.discover():
                raise ServiceError('NEMOTRON_NOT_DISCOVERED')
            session.ready=True
            name,call,call_id=session.select('smoke',{'smoke_ack':SmokeAck},
                {'verification_nonce':uuid4().hex},trace)
            receipt.update(status='passed',output=call.model_dump(mode='json'),call_id=call_id,
                model_receipt=session.response_receipts[-1],live_verified=True)
        except (ServiceError,ValueError) as exc:
            receipt.update(status='failed',error_code=exc.code if isinstance(exc,ServiceError) else 'SMOKE_VALIDATION_FAILED')
        receipt.update(budget=budget.summary(),http_records=[{k:v for k,v in t.items() if k not in {'start','settled'}} for t in budget.tickets])
        (args.output_dir/'smoke.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps(receipt,indent=2),flush=True)
        if not receipt['live_verified']:
            return 1
        if not args.smoke_only:
            from .helene_model_verification import run_roles
            return run_roles(args,client,approval,budget)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
