"""Explicit live read diagnostics. Never dispatches inference or prints credentials."""
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import httpx
from contracts import DataQuery
from phases.phase_01_foundation.config import load_env
from phases.phase_01_foundation.http import ServiceError, make_client, request_json
from phases.phase_01_foundation.nightshift import Nightshift
from phases.phase_02_data.pipeline import load_bundle
from phases.phase_02_data.store import DataStore
from .integration import ROOT


def live_diagnostics(output, *, env_file=None, zetaris_env_file=None):
    load_env(env_file)
    load_env(zetaris_env_file,prefix='ZETARIS_')
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'inference_posts':0,
            'live_investigation':'unverified','inference_blocker':'No independently confirmed free route; paid inference not authorized.',
            'prepared_budget':{'max_calls':4,'output_tokens_per_call':300,'max_output_tokens':1200,
                'max_input_bytes_per_call':24000,'max_response_bytes_per_call':32000,
                'request_timeout_seconds':60,'retries':0,'fallback':False}}
    with make_client() as client:
        provider=Nightshift(client)
        result['selected_model']=(provider.model if re.fullmatch(r'nvidia/[a-zA-Z0-9_./-]+',provider.model) and
            'nemotron' in provider.model.lower() else 'unconfigured-or-invalid')
        result['tool_compatibility']='unverified live; strict native function-call and structured-response validation implemented'
        result['route_identity']='unverified live; requires actual response winning-route model/provider metadata'
        try:
            body,_=request_json(client,'GET',provider.base.removesuffix('/v1')+'/healthz',max_bytes=32000)
            result['nightshift_health']={'status':'reachable','ok':isinstance(body,dict) and body.get('status')=='ok'}
        except ServiceError as exc:
            result['nightshift_health']={'status':'blocked','error_code':exc.code}
        try:
            ids=provider.discover()
            result['catalog']={'status':'reachable','model_count':len(ids),
                'nemotron_ids':[i for i in ids if 'nemotron' in i.lower()],
                'selected_model':provider.model,'selected_present':provider.model in ids,
                'routing_metadata':'Winning route can be verified only from an actual inference response.',
                'cost_metadata':'Adapter catalog exposes IDs; no verified free-route guarantee.'}
        except ServiceError as exc:
            result['catalog']={'status':'blocked','error_code':exc.code}
        try:
            bundle=load_bundle(ROOT/'docs/evidence/pharmax-phase2/dataset.json')
            page=DataStore(client,bundle).query(DataQuery(collection='products',limit=2))
            result['zetaris']={'status':'verified-stored-records','dataset_version':page.dataset_version,
                'rows':len(page.items),'product_ids':[p.product_id for p in page.items],
                'mode':page.mode,'evidence_ids':[e.evidence_id for e in page.evidence]}
        except (ServiceError,ValueError,httpx.HTTPError) as exc:
            result['zetaris']={'status':'blocked','error_code':exc.code if isinstance(exc,ServiceError) else 'STORED_READ_FAILED'}
    Path(output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result
