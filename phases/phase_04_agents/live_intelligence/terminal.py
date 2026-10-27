"""Three-minute source-to-decision demonstration, replay, refresh, poll and read API."""
import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import time
import httpx
from phases.phase_01_foundation.config import load_env
from phases.phase_01_foundation.http import ServiceError
from phases.phase_02_data.snapshots import Snapshots
from phases.phase_04_agents.coordinator import investigate_signal
from phases.phase_04_agents.trace import Trace
from .registry import Registry, investigation_key
from .scenario import build_current
from .matching import match_dependency
from .sources import Fetcher, frozen
from .polling import refresh
from .zetaris import SignalStore
from .service import serve

ROOT=Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST=ROOT/'docs/evidence/pharmax-phase4c/manifest.json'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group()
    group.add_argument('--live-fetch',action='store_true')
    group.add_argument('--refresh',action='store_true',help='Fetch/normalize only; no investigation.')
    group.add_argument('--poll',action='store_true',help='Run local due-check loop; never auto-infer or investigate.')
    group.add_argument('--investigate-pending',action='store_true',help='Consume one retained relevant job, with no source/model HTTP calls.')
    group.add_argument('--serve',action='store_true',help='Loopback read-only API; no poller.')
    p.add_argument('--replay',type=Path,default=DEFAULT_MANIFEST)
    p.add_argument('--output-dir',type=Path,default=ROOT/'.runtime/pharmax-p4c')
    p.add_argument('--manifest-name',default='manifest.json',help='Unique refresh receipt filename within output directory.')
    p.add_argument('--zetaris-env-file')
    p.add_argument('--publish-zetaris',action='store_true')
    p.add_argument('--only-source',choices=['both','marad','gdelt'],default='both')
    p.add_argument('--delays',default='0,3,7')
    p.add_argument('--marad-seconds',type=int,default=21600)
    p.add_argument('--gdelt-seconds',type=int,default=1800)
    p.add_argument('--node')
    p.add_argument('--port',type=int,default=8766)
    args=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.json',args.manifest_name):
        p.error('Manifest must be a JSON filename')
    live=args.live_fetch or args.refresh or args.poll
    if not live and (args.publish_zetaris or args.zetaris_env_file):
        p.error('Frozen replay/read service cannot contact Zetaris or read credentials')
    if not all(300<=n<=86400 for n in (args.marad_seconds,args.gdelt_seconds)):
        p.error('Polling cadence must be 300..86400 seconds')
    delays=tuple(int(d) for d in args.delays.split(','))
    if not delays or len(delays)>5 or len(set(delays))!=len(delays) or 0 not in delays or any(not 0<=d<=21 for d in delays):
        p.error('Use 1..5 unique delay days in 0..21 including zero')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    registry=Registry(args.output_dir/'events.sqlite')
    if args.serve:
        serve(registry,args.port)
        return 0
    sources=('marad','gdelt') if args.only_source=='both' else (args.only_source,)
    manifest={}
    errors=[]
    if live:
        load_env(args.zetaris_env_file,prefix='ZETARIS_')
        snapshots=Snapshots(args.output_dir/'snapshots')
        with httpx.Client(timeout=httpx.Timeout(20,connect=4),follow_redirects=False,
                          headers={'User-Agent':'PharmaX-Intelligence/0.1'}) as client:
            while True:
                now=datetime.now(timezone.utc)
                # A stable, versioned day-start scenario; never later than this actual check.
                anchor=now.date()
                if anchor.month!=10 or anchor.year!=2026:
                    anchor=datetime(2026,10,24).date()
                bundle,dependency=build_current(anchor,now)
                fetcher=Fetcher(snapshots,client)
                results=refresh(registry,fetcher,now,sources=sources,force=not args.poll,dependency=dependency,delays=delays,
                                cadences={'marad':args.marad_seconds,'gdelt':args.gdelt_seconds})
                for result in results:
                    print('PUBLIC SOURCE '+json.dumps(result),flush=True)
                errors=[r for r in results if r['status']=='failed']
                manifest={'schema_version':'4c.1','scenario_anchor':str(anchor),
                    'fetch_status':results,'source_status':[s.model_dump(mode='json') for s in registry.statuses()],
                    'marad':[],'gdelt':[],'zetaris':{'mode':'not-requested'}}
                for result in results:
                    if result['status']=='success':
                        manifest[result['source']]=result['receipts']
                if args.publish_zetaris and (not args.poll or any(r.get('new_revisions') for r in results)):
                    try:
                        if not registry.revisions():
                            raise ServiceError('NO_NORMALIZED_SIGNALS')
                        # Publish the actual successful retrievals from this cycle without mutating prior registry revisions.
                        publication={s.revision_id:s for s in registry.revisions()}
                        publication.update({s.revision_id:s for s in frozen(snapshots,manifest)})
                        manifest['zetaris']=SignalStore(client).publish(list(publication.values()))
                        print('ZETARIS STORE/QUERY '+json.dumps(manifest['zetaris']),flush=True)
                    except (ServiceError,ValueError) as exc:
                        code=exc.code if isinstance(exc,ServiceError) else 'ZETARIS_VERIFICATION_FAILED'
                        manifest['zetaris']={'mode':'live-zetaris','verified':False,'error_code':code}
                        errors.append({'source':'zetaris','error_code':code})
                        print('ZETARIS BLOCKER '+code,flush=True)
                retained=registry.latest(datetime.now(timezone.utc))
                marad=next((s for s in retained if s.provider=='marad'),None)
                gdelt=next((s for s in retained if s.provider=='gdelt'),None)
                if marad and not manifest['marad']:
                    manifest['marad']=[e.raw_ref.split(':',1)[1] for e in marad.evidence]
                if gdelt and not manifest['gdelt']:
                    manifest['gdelt']=[gdelt.evidence[0].raw_ref.split(':',1)[1]]
                manifest['snapshot_semantics']='Retained last-success records; current failures are in source_status. No failed fetch is replaced in live investigation dispatch.'
                (args.output_dir/args.manifest_name).write_text(json.dumps(manifest,indent=2)+'\n')
                if args.poll:
                    print('LOCAL POLLER RUNNING; no investigations or model calls; monitoring stops with this process.',flush=True)
                    time.sleep(30)
                    continue
                break
        if args.refresh:
            return 2 if errors else 0
        mode='live-fetch'
        signals=registry.latest(datetime.now(timezone.utc))
        # Only revisions actually fetched this cycle can start a new expensive investigation.
        relevant={r for item in results if item['status']=='success' for r in item['new_revisions']}
        signals=[s for s in signals if s.revision_id in relevant]
    elif args.investigate_pending:
        mode='frozen-replay'
        signals=registry.latest(datetime.now(timezone.utc))
        if not signals:
            print('No retained signal to investigate.',flush=True)
            return 0
        cutoff=max(s.retrieved_at for s in signals)
        bundle,dependency=build_current(cutoff.date(),cutoff)
        stored=args.output_dir/'manifest.json'
        manifest=json.loads(stored.read_text()) if stored.exists() else {}
        print('RETAINED REGISTRY WORKER: no HTTP/model calls; at most one claimed investigation.',flush=True)
    else:
        manifest=json.loads(args.replay.read_text())
        snapshots=Snapshots(args.replay.parent/'snapshots')
        signals=frozen(snapshots,manifest)
        registry.ingest(signals)
        for status in manifest.get('source_status',[]):
            registry.set_status(status)
        cutoff=max(s.retrieved_at for s in signals)
        anchor=datetime.fromisoformat(manifest['scenario_anchor']).date()
        bundle,dependency=build_current(anchor,cutoff)
        mode='frozen-replay'
        print('FROZEN OFFLINE REPLAY: retained real-source receipts; zero HTTP/Zetaris/model calls.',flush=True)
    available=registry.latest(max([s.retrieved_at for s in signals],default=datetime.now(timezone.utc)))
    selected=next((s for s in signals if match_dependency(s,dependency,s.retrieved_at,
                    available_signals=available).status=='MATCHED'),None)
    if selected is None:
        print('NO NEW VERIFIED RELEVANT SIGNAL: no forecast/agent dispatch.',flush=True)
        return 2 if errors else 0
    key=investigation_key(selected,dependency,delays)
    registry.queue(key)
    if not registry.claim(key):
        print('INVESTIGATION ALREADY CLAIMED/COMPLETED; no repeated expensive dispatch.',flush=True)
        return 0
    receipt=manifest.get('zetaris',{}) if live else {'mode':'frozen-replay','prior_live_receipt':manifest.get('zetaris',{})}
    trace=Trace(emit=lambda line:print(line,flush=True))
    run=investigate_signal(selected,bundle,dependency,args.output_dir/'memory',mode=mode,
        decision_at=selected.retrieved_at,delays=delays,trace=trace,node=args.node,available_signals=available,zetaris=receipt)
    registry.finish(key,run)
    (args.output_dir/'investigation.json').write_text(run.model_dump_json(indent=2)+'\n')
    print(f'RESULT {run.status}; observed signal={selected.event_id}; simulated dependency={dependency.shipment_id}; mode={mode}',flush=True)
    if run.mitigation:
        print('HUMAN REVIEW: '+str(run.mitigation.recommendation),flush=True)
    return 0 if run.status=='complete' and not errors else 2
