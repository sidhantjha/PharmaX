"""Single terminal entry point: investigation, report, source refresh and HTTP service."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4
from contracts.operator import CaseRequest
from phases.phase_04_agents.live_intelligence.service import serve
from .integration import MANIFEST
from .service import OperatorService
from .reports import report, forecasts


def render(case, verbose=False):
    lines=[f'PharmaX | {case.scenario} | {case.mode} | {case.created_at.isoformat()}',
           f'Investigation {case.investigation_id} | {case.status.upper()}',
           ('NIGHTSHIFT COORDINATOR ATTEMPT; inspect status and route traces.' if case.model_execution=='nightshift' else
            'LOCAL DETERMINISTIC SPECIALISTS; real tool execution and H-MEM; no live LLM conversation.'),
           '\nSOURCE INGESTION / VERIFICATION']
    if case.revision:
        lines.extend(['\nEVIDENCE RECONCILIATION / REVISION',case.revision.decision_summary,
                      'Disposition: '+case.revision.disposition])
        if case.revision.disposition=='unresolved':
            lines.append('No revised action adopted. Forecast and mitigation below retain the previous assumption conditionally; drafts withheld.')
    if case.intelligence:
        run=case.intelligence
        lines.extend([run.signal.title, f'Simulated shipment {run.dependency.shipment_id}: '
            f'{run.dependency.quantity_vials} vials {run.dependency.product_id}; supplier {run.dependency.supplier_id}; '
            f'arrival {run.dependency.planned_arrival}; assumed +{run.selected_delay_days} days.',
            f'Match {run.match.status}; no confirmed shipment delay.'])
    lines.extend(f'[{v.status.upper()}] {v.check}: {v.explanation}' for v in case.verification)
    lines.extend(f'CITATION [{e.kind}] {e.source_url} retrieved={e.observed_at.isoformat()}'
                 for e in case.evidence if e.kind=='observed')
    lines.append('\nHOSPITAL INVENTORY / FORECAST (vials)')
    lines.append(f'{"Case":12} {"Hospital":14} {"Initial":>8} {"Unmet":>8} {"Ending":>8}  First unmet')
    for label,f in forecasts(case):
        lines.append(f'{label:12} {f.request.hospital_id:14} {f.initial_usable:8} {f.total_unmet:8} '
                     f'{f.points[-1].closing_usable:8}  {f.projected_stockout_date}')
    lines.append('\nACTUAL PARTICIPANTS / TOOL AND MEMORY ACTIVITY')
    chronological=[]
    for agent in case.agents:
        lines.append(f'[{agent.status.upper()}] {agent.agent_id}: {len(agent.tool_records)} records; '
                     f'{len(agent.memory_ids)} memory IDs. {agent.responsibility}')
        chronological.extend(agent.tool_records)
    if verbose:
        lines.append('\nCHRONOLOGICAL PUBLIC TOOL TRACE')
        lines.extend(json.dumps(event,sort_keys=True) for event in sorted(chronological,key=lambda e:e['sequence']))
    lines.append('\nEXECUTION GRAPH / RECORDED OVERLAP')
    for task in case.execution_graph:
        lines.append(f'{task.agent_id} [{task.status}] {task.task_id}: '
                     f'{task.started_at} → {task.completed_at}; dependencies={task.dependency_ids}')
    overlaps = []
    for index, left in enumerate(case.execution_graph):
        for right in case.execution_graph[index+1:]:
            # Parent envelopes describe coordination, not simultaneous specialist work.
            if (left.parent_id == right.parent_id and left.parent_id is not None
                    and left.started_at and left.completed_at and right.started_at and right.completed_at
                    and max(left.started_at, right.started_at) < min(left.completed_at, right.completed_at)):
                overlaps.append(f'{left.agent_id} ({left.task_id}) ↔ {right.agent_id} ({right.task_id})')
    lines.extend('OBSERVED OVERLAP '+pair for pair in overlaps)
    if not overlaps:
        lines.append('No specialist overlap recorded in this run.')
    lines.append('Source gates, reconciliation, H-MEM writes and mitigation decisions run sequentially.')
    lines.append('\nMITIGATION ALTERNATIVES (independent evaluations)')
    for e in case.mitigation.evaluations:
        lines.append(f'[{"FEASIBLE" if e.feasible else "REJECTED"}] {e.candidate.candidate_id}: '
                     f'{e.original_unmet} → {e.remaining_unmet} unmet vials; '
                     + ', '.join(f.code for f in e.failures))
        if e.feasible:
            lines.extend(f'  {i.hospital_id}: {i.effective.unmet_vials} unmet / {i.effective.risk}; donor={i.donor}' for i in e.impacts)
    lines.append('\nSYNTHETIC CLINICAL SCHEDULING')
    lines.extend(f'{i.case} / {i.hospital_id}: {i.unique_exposed_patients} unique modeled patients; '
                 f'{i.exposed_sessions} exposed sessions; first {i.first_unmet_on}' for i in case.clinical_impact)
    lines.append('Vials are modeled preparation requirements, not patient counts, verified doses or outcomes.')
    lines.append('\nPREPARED OPERATOR ACTIONS — HUMAN REVIEW REQUIRED')
    lines.extend(f'[{a.status}] {a.kind}: {a.quantity} {a.quantity_unit}; {a.action_id}' for a in case.actions)
    lines.extend(['\nFINAL RECOMMENDATION',case.mitigation.recommendation or 'Unavailable',
                  '\nRESIDUAL RISKS / LIMITATIONS',*case.limitations])
    return '\n'.join(lines)+'\n'


def main(*, model_authorization=None, model_client_factory=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenario',choices=['red-sea','original','clean-onset'],default='red-sea')
    p.add_argument('--mode',choices=['replay','refresh'],default='replay')
    p.add_argument('--delay-days',type=int,default=7)
    p.add_argument('--verbose',action='store_true')
    p.add_argument('--conflict-demo',action='store_true',help='Replay evidence conflicts, immutable revision and adaptive mitigation')
    p.add_argument('--serve',action='store_true')
    p.add_argument('--diagnostics',action='store_true')
    p.add_argument('--live-diagnostics',action='store_true')
    p.add_argument('--live-model',action='store_true')
    p.add_argument('--live-specialists',action='store_true',help='Opt-in model-directed verification, dependency and mitigation; requires backend-owned reviewed authorization')
    p.add_argument('--env-file')
    p.add_argument('--node')
    p.add_argument('--only-source',choices=['both','marad','gdelt'],default='both')
    p.add_argument('--publish-zetaris',action='store_true')
    p.add_argument('--zetaris-env-file')
    p.add_argument('--port',type=int,default=8766)
    p.add_argument('--output-dir',type=Path,default=Path('.runtime/pharmax-operator'))
    args=p.parse_args()
    if not args.live_diagnostics and args.mode=='replay' and (args.publish_zetaris or args.zetaris_env_file or args.live_model or (args.env_file and not args.live_specialists)):
        p.error('Replay cannot contact Zetaris or read credentials')
    if args.live_model:
        print('NIGHTSHIFT BLOCKED: no independently verified no-cost route/runtime controls; no inference dispatched.')
        return 2
    if args.scenario!='red-sea' and args.mode!='replay':
        p.error('Historical scenarios use frozen replay only; source refresh belongs to the October pilot')
    if args.serve and args.mode!='replay':
        p.error('Server startup cannot refresh sources')
    if args.conflict_demo and (args.mode!='replay' or args.scenario!='red-sea' or args.delay_days!=7
            or args.serve or args.diagnostics or args.live_diagnostics):
        p.error('Conflict demo requires the original seven-day Red Sea offline replay')
    if args.live_specialists and (args.mode!='replay' or args.scenario!='red-sea' or args.serve
            or args.conflict_demo or args.diagnostics or args.live_diagnostics):
        p.error('Live specialists require the retained October case; source refresh and offline replay are separate')
    if args.live_specialists:
        from phases.phase_01_foundation.config import load_env
        load_env(args.env_file, prefix='METERLESS_')
    service=OperatorService(args.output_dir,node=args.node, model_authorization=model_authorization,
        model_client_factory=model_client_factory)
    if args.conflict_demo:
        from .conflict_demo import demonstrate
        return demonstrate(service,args.output_dir)
    if args.live_diagnostics:
        from .diagnostics import live_diagnostics
        live_diagnostics(args.output_dir/'live-diagnostics.json',env_file=args.env_file,zetaris_env_file=args.zetaris_env_file)
        return 0
    if args.serve:
        print(f'Operator endpoints http://127.0.0.1:{args.port}/api/v1/health',flush=True)
        serve(service.registry,args.port,operator=service)
        return 0
    if args.diagnostics:
        status,body=service.handle('GET','/api/v1/health')
        print(json.dumps(body,indent=2))
        return 0 if status==200 else 2
    manifest=MANIFEST
    if args.mode=='refresh':
        # Reuse bounded refresh with a unique receipt and durable provider backoff; require fresh MARAD.
        import subprocess
        target=args.output_dir/'refresh'
        manifest=target/('manifest-'+uuid4().hex+'.json')
        command=[sys.executable,'scripts/live_intelligence_demo.py','--refresh','--output-dir',str(target),
                 '--only-source',args.only_source,'--manifest-name',manifest.name]
        if args.publish_zetaris:
            command.append('--publish-zetaris')
        if args.zetaris_env_file:
            command.extend(['--zetaris-env-file',args.zetaris_env_file])
        result=subprocess.run(command,check=False)
        receipt=json.loads(manifest.read_text()) if manifest.exists() else {}
        fresh_marad=any(s['source']=='marad' and s['status']=='success' for s in receipt.get('fetch_status',[]))
        if not fresh_marad:
            print('REFRESH BLOCKED: no fresh MARAD signal; no cached substitution. Explicit replay remains available.')
            return result.returncode or 2
        if result.returncode:
            print('REFRESH PARTIAL: fresh MARAD available; failed sources remain unavailable. No cached substitution.')
    if args.live_specialists:
        from phases.phase_01_foundation.http import ServiceError
        try:
            case=service.create_live(CaseRequest(scenario=args.scenario,delay_days=args.delay_days))
        except ServiceError as exc:
            print('LIVE SPECIALIST INVESTIGATION BLOCKED: '+exc.code+'; no offline substitute.')
            return 2
    else:
        case=service.create(CaseRequest(scenario=args.scenario,delay_days=args.delay_days),
            manifest_path=manifest,live_source=args.mode=='refresh')
    output=args.output_dir/case.investigation_id
    output.mkdir(parents=True,exist_ok=True)
    exported=report(case)
    transcript=render(case,args.verbose)
    for name,body in [('investigation.json',case.model_dump_json(indent=2)+'\n'),
                      ('briefing.md',exported['markdown']),('forecast.csv',exported['csv']),('terminal.txt',transcript)]:
        (output/name).write_text(body)
    print(transcript,end='')
    print(f'ARTIFACTS {output}',flush=True)
    return 0 if case.status=='complete' else 2
