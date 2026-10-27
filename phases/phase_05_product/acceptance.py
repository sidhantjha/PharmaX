"""Actual-process HTTP acceptance and generated frontend examples. No network except loopback."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4
import httpx
from contracts.operator import ApiResponse, OperatorCase, ActionPackage, ChatResponse, ApiError
from .integration import ROOT
from .conflict_demo import comparison


def validate_workflow(payload, *, revised=False):
    """Check calculated outputs and actual dependency records, beyond HTTP schemas."""
    case = OperatorCase.model_validate(payload)
    assert case.status == 'complete'
    summary = comparison(case)
    assert summary['unmet_vials'] == {'baseline': 0, 'disruption': 30 if revised else 24, 'mitigation': 0}
    assert summary['recommendation_id'] == ('lakes-additional' if revised else 'north-conservative')
    if not revised:
        assert summary['synthetic_patient_exposure']['disruption'] == 12
    assert summary['synthetic_patient_exposure']['mitigation'] == 0
    chosen = next(e for e in case.mitigation.evaluations if e.candidate.candidate_id == summary['recommendation_id'])
    assert chosen.feasible and chosen.remaining_unmet == 0
    assert chosen.quantity_moved == (36 if revised else 24)
    assert all(i.effective.unmet_vials == 0 for i in chosen.impacts if i.donor)
    assert any(not e.feasible and e.failures for e in case.mitigation.evaluations)
    if revised:
        assert case.revision.previous_option_recheck.remaining_unmet == 6
        assert case.parent_id and case.conflict_resolution.conflicts
        assert case.revision.prior_constraint_memory_refs
    tasks = {t.task_id: t for t in case.execution_graph}
    for task in tasks.values():
        assert task.started_at and task.completed_at and task.started_at <= task.completed_at
        for dependency in task.dependency_ids:
            assert tasks[dependency].completed_at <= task.started_at
    overlaps = [(a.agent_id, b.agent_id) for index, a in enumerate(case.execution_graph)
                for b in case.execution_graph[index + 1:]
                if a.read_only and b.read_only and a.parent_id == b.parent_id and a.parent_id
                and max(a.started_at, b.started_at) < min(a.completed_at, b.completed_at)]
    assert overlaps, 'No actual read-only specialist overlap recorded'
    memory = [r for r in case.decision_ledger if r.operation == 'hmem.exchange' and r.status == 'complete']
    assert memory and any(r.summary.get('writes') for r in memory)
    assert any(r.memory_ids for r in memory)
    assert all(a.completed_at <= b.started_at for a, b in zip(memory, memory[1:]))
    assert all(a.status == 'DRAFT' and a.quantity_unit == 'vial' for a in case.actions)
    evidence = {e.evidence_id for e in case.evidence}
    assert all(i in evidence for r in case.decision_ledger for i in r.evidence_ids)
    assert all(i in evidence for a in case.actions for i in a.evidence_ids)
    assert all(e.observed_at.tzinfo and len(e.content_sha256) == 64 for e in case.evidence)
    return {**summary, 'read_only_overlaps': overlaps, 'hmem_exchanges': len(memory),
            'dependencies': 'passed', 'donor_safety': 'passed'}


def main(output=None, *, evidence_conflicts=False):
    output=Path(output) if output else ROOT/'docs/evidence/pharmax-phase5'
    examples=output/'api'
    examples.mkdir(parents=True,exist_ok=True)
    runtime=ROOT/'.runtime'/('pharmax-phase5-acceptance-'+uuid4().hex)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port=probe.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'base_url':base,'http_checks':[],
            'validation':'Pydantic contracts generated into JSON Schema; every successful HTTP body validated.',
            'external_execution':'none; all actions remain draft or synthetic acceptance review',
            'network':'loopback only in this acceptance process'}
    with (output/'api-process.txt').open('w') as log:
        process=subprocess.Popen([sys.executable,'scripts/pharmax_demo.py','--serve','--port',str(port),
            '--output-dir',str(runtime)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy())
        try:
            with httpx.Client(base_url=base,timeout=120,trust_env=False) as client:
                for _ in range(60):
                    try:
                        ready=client.get('/api/v1/health')
                        if ready.status_code==200:
                            break
                    except httpx.RequestError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError('API process exited')
                    time.sleep(0.1)
                else:
                    raise RuntimeError('API startup timeout')

                def call(method,path,name,*,payload=None,expected=200,model=ApiResponse):
                    response=client.request(method,path,json=payload) if payload is not None else client.request(method,path)
                    assert response.status_code==expected,(path,response.status_code,response.text[:300])
                    body=response.json()
                    if model and expected<300:
                        model.model_validate(body)
                    if expected>=400:
                        ApiError.model_validate(body)
                    (examples/(name+'.json')).write_text(json.dumps(body,indent=2)+'\n')
                    result['http_checks'].append({'method':method,'path':path,'status':response.status_code,'example':f'api/{name}.json'})
                    return body

                def check_projection(snapshot, label):
                    identity=snapshot['investigation_id']
                    # Read every page, including ledger records beyond the first 100.
                    for section,field in (('agents','agents'),('evidence','evidence'),
                            ('execution-graph','execution_graph'),('decision-ledger','decision_ledger')):
                        rows=[]
                        for offset in range(0,len(snapshot[field]),100):
                            page=call('GET',f'/api/v1/investigations/{identity}/{section}?limit=100&offset={offset}',
                                      f'{label}-{section}-page-{offset}')
                            assert page['investigation_id']==identity
                            assert page['total']==len(snapshot[field]) and page['offset']==offset and page['limit']==100
                            rows.extend(page['items'])
                        assert rows==snapshot[field], (label,section,'projection differs from saved case')

                case=call('POST','/api/v1/investigations','create',payload={'scenario':'red-sea'},expected=201,model=OperatorCase)
                identity=case['investigation_id']
                result['investigation_id']=identity
                result['original_workflow']=validate_workflow(case)
                check_projection(case,'original')
                revised=None
                if evidence_conflicts:
                    revised=call('POST',f'/api/v1/investigations/{identity}/reconcile','reconcile',
                        payload={},expected=201,model=OperatorCase)
                    result['revised_investigation_id']=revised['investigation_id']
                    result['revised_workflow']=validate_workflow(revised, revised=True)
                    check_projection(revised,'revised')
                    for section in ('conflicts','resolution','revision','forecast-deltas','execution-graph','decision-ledger'):
                        call('GET',f'/api/v1/investigations/{revised['investigation_id']}/{section}?limit=100','revised-'+section)
                    retained=call('GET',f'/api/v1/investigations/{identity}','parent-after-revision')
                    assert retained['items'][0]==case
                    assert revised['revision']['disposition']=='revised'
                    result['evidence_conflict_revision']='passed'
                    revised_id=revised['investigation_id']
                    for section in ('agents','evidence','forecast','clinical-impact','mitigations','actions','report'):
                        call('GET',f'/api/v1/investigations/{revised_id}/{section}?limit=100','revised-'+section)
                    for resource in ('medicines','hospitals','sources/status','disruptions','map/layers'):
                        call('GET',f'/api/v1/{resource}?investigation_id={revised_id}&limit=100',
                             'revised-'+resource.replace('/','-'))
                    revised_chat=call('POST','/api/v1/chat','revised-chat',payload={
                        'investigation_id':revised_id,'question':'Why is carboplatin at risk?'},model=ChatResponse)
                    assert revised_chat['investigation_id']==revised_id
                    assert revised_chat['execution_state']=='deterministic-offline-replay'
                for resource in ('health','sources/status','medicines','disruptions','map/layers','hospitals','investigations'):
                    call('GET',f'/api/v1/{resource}?investigation_id={identity}&limit=100',resource.replace('/','-'))
                call('GET',f'/api/v1/investigations/{identity}','investigation')
                for resource in ('agents','execution-graph','decision-ledger','evidence','forecast','clinical-impact','mitigations','actions','report'):
                    call('GET',f'/api/v1/investigations/{identity}/{resource}?limit=100',resource)
                for fmt in ('csv','markdown','pdf'):
                    call('GET',f'/api/v1/investigations/{identity}/report?format={fmt}','report-'+fmt)
                call('GET',f'/api/v1/investigations/{identity}/agents?limit=1&offset=1','pagination')
                action=next(a for a in case['actions'] if a['kind']=='stock_transfer')
                review={'expected_revision':1,'status':'READY_FOR_REVIEW','operator':'synthetic acceptance reviewer',
                        'note':'Acceptance fixture: inspected quantity and simulation labels; no real authorization.',
                        'attestations':['Draft reviewed for accuracy and simulation labels']}
                path=f'/api/v1/investigations/{identity}/actions/{action["action_id"]}/review'
                call('POST',path,'action-reviewed',payload=review,model=ActionPackage)
                call('POST',path,'action-conflict',payload=review,expected=409,model=None)
                call('GET',f'/api/v1/investigations/{identity}/actions','actions-after-review')
                # Original immutable snapshot still contains DRAFT actions.
                reopened=call('GET',f'/api/v1/investigations/{identity}','reopened')
                assert reopened['items'][0]['actions']==case['actions']
                for index,question in enumerate(('Why is carboplatin at risk?','How credible is that source?',
                        'How many modeled patients may be affected?','Which options were rejected and why?',
                        'Draft a transfer request.','What happens if delivery is three days later?')):
                    chat=call('POST','/api/v1/chat',f'chat-{index}',payload={'investigation_id':identity,'question':question},model=ChatResponse)
                    assert chat['execution_state']=='deterministic-offline-replay'
                    if index==5:
                        child=call('GET',f'/api/v1/investigations/{chat["investigation_id"]}','versioned-scenario')
                        assert child['items'][0]['parent_id']==identity
                blocked=call('POST','/api/v1/chat','chat-live-blocked',payload={
                    'investigation_id':identity,'question':'Show risk','execution_mode':'live'},model=ChatResponse)
                assert blocked['execution_state']=='inference-not-authorized'
                assert blocked['error_code']=='NO_COST_ROUTE_UNCONFIRMED' and not blocked['decisions']
                for path,expected in (('/api/v1/investigations/missing',404),('/api/v1/medicines?limit=101',400),
                    ('/api/v1/health?unknown=x',400),('/api/v1/unknown',404),
                    ('/api/v1/investigations/'+identity+'/report?format=html',400)):
                    call('GET',path,'error-'+str(len(result['http_checks'])),expected=expected,model=None)
                call('POST','/api/v1/investigations','invalid-create',payload={'scenario':'invented'},expected=422,model=None)
                call('POST','/api/v1/chat','invalid-chat',payload={'question':'x'},expected=422,model=None)
                for name,content in [('invalid-json','{'),('oversized-json','x'*17000)]:
                    response=client.post('/api/v1/chat',content=content,headers={'Content-Type':'application/json'})
                    assert response.status_code==400
                    ApiError.model_validate(response.json())
                    (examples/(name+'.json')).write_text(json.dumps(response.json(),indent=2)+'\n')
                    result['http_checks'].append({'method':'POST','path':'/api/v1/chat','status':400,'example':f'api/{name}.json'})
                sent={**review,'expected_revision':2,'status':'SENT'}
                call('POST',f'/api/v1/investigations/{identity}/actions/{action["action_id"]}/review',
                     'dispatch-denied',payload=sent,expected=422,model=None)
                call('POST','/api/v1/chat','chat-create',payload={'question':'Why is carboplatin at risk?'},model=ChatResponse)
                # Exercise every retained legacy resource on the same actual HTTP process.
                legacy={}
                for resource in ('signals','sources','citations','matches','risk','forecasts','activity','mitigations'):
                    response=client.get('/v1/intelligence/'+resource+'?limit=1')
                    assert response.status_code==200
                    body=response.json()
                    assert body['schema_version']=='4c.1' and isinstance(body['items'],list)
                    legacy[resource]=response.status_code
                    (examples/('legacy-'+resource+'.json')).write_text(json.dumps(body,indent=2)+'\n')
                result['legacy_status']=200
                result['legacy_http_checks']=legacy
                # Stop/restart the actual process; immutable case and separate action revisions must survive.
                process.terminate()
                process.wait(timeout=10)
                process=subprocess.Popen([sys.executable,'scripts/pharmax_demo.py','--serve','--port',str(port),
                    '--output-dir',str(runtime)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy())
                for _ in range(60):
                    try:
                        if client.get('/api/v1/health').status_code==200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError('Restart timeout')
                restarted=call('GET',f'/api/v1/investigations/{identity}','retrieved-after-process-restart')
                assert restarted['items'][0]==case
                revised_actions=call('GET',f'/api/v1/investigations/{identity}/actions','actions-after-process-restart')
                assert next(a for a in revised_actions['items'] if a['kind']=='stock_transfer')['revision']==2
                if revised:
                    persisted=call('GET',f'/api/v1/investigations/{revised['investigation_id']}','revision-after-process-restart')
                    assert persisted['items'][0]==revised
                    result['revised_case_persistence']='passed'
                result['persistence_after_process_restart']='passed'
                result['status']='passed'
        finally:
            process.terminate()
            process.wait(timeout=10)
    (output/'http-acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS: {len(result["http_checks"])} actual HTTP checks; legacy={result["legacy_status"]}; case={identity}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--evidence-conflicts',action='store_true')
    args=parser.parse_args()
    main(args.output_dir,evidence_conflicts=args.evidence_conflicts)
