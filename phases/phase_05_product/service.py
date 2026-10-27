"""Versioned bounded operator API mounted into the existing intelligence server."""
from datetime import datetime, timezone
from pathlib import Path
import threading
from urllib.parse import urlsplit,parse_qs
from pydantic import ValidationError
from contracts.operator import (ApiResponse, Health, MapLayer, CaseRequest, ChatRequest, ActionReview, ForecastSeries, ReportExport, SourceHealth)
from phases.phase_04_agents.live_intelligence.registry import Registry
from phases.phase_04_agents.live_intelligence.service import IntelligenceService
from .integration import run_case
from .revision import revise_case
from contracts.reconciliation import ReconciliationRequest
from .store import CaseStore
from .reports import forecasts, report
from .chat import answer


class OperatorService:
    def __init__(self, root, *, node=None, model_authorization=None, model_client_factory=None):
        from .model_chat import model_client
        self.model_authorization = model_authorization
        self.model_client_factory = model_client_factory or model_client
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.store=CaseStore(self.root/'operator.sqlite')
        from phases.phase_06_helene.service import HeleneStore
        self.helene_store=HeleneStore(self.store)
        self.registry=Registry(self.root/'events.sqlite')
        self.legacy=IntelligenceService(self.registry)
        self.node=node
        self.execution_lock=threading.Lock()
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY, created TEXT, request TEXT, response TEXT)')

    def save_chat(self, request, response):
        with self.store.connect() as db:
            db.execute('INSERT INTO chat(created,request,response) VALUES (?,?,?)',
                (datetime.now(timezone.utc).isoformat(),request.model_dump_json(),response.model_dump_json()))

    def create(self, request, **kwargs):
        if request.parent_id:
            self.store.get(request.parent_id)
        if not self.execution_lock.acquire(blocking=False):
            raise ValueError('INVESTIGATION_BUSY')
        try:
            case=run_case(request,self.root/'memory',node=self.node,**kwargs)
            return self.store.save(case)
        finally:
            self.execution_lock.release()

    def create_live(self, request):
        """Backend-owned opt-in; HTTP/default replay cannot manufacture approval."""
        import os
        from phases.phase_01_foundation.http import ServiceError
        from phases.phase_01_foundation.nightshift import runtime_base
        from .live_specialists import LiveSpecialists, ModelSession
        if request.scenario != 'red-sea' or request.parent_id:
            raise ServiceError('LIVE_SPECIALIST_SCENARIO_DENIED')
        approval = self.model_authorization
        if approval is None:
            raise ServiceError('NO_COST_ROUTE_UNCONFIRMED', blocked=True)
        endpoint = runtime_base(os.environ.get('METERLESS_BASE_URL', 'http://127.0.0.1:8088/v1'))
        model = os.environ.get('METERLESS_MODEL', '')
        approval.require(endpoint, model)
        if model != 'nvidia/nemotron-3.5-lightning-30b-a3b':
            raise ServiceError('NEMOTRON_PIN_REQUIRED')
        if not self.execution_lock.acquire(blocking=False):
            raise ValueError('INVESTIGATION_BUSY')
        try:
            with self.model_client_factory() as client:
                session = ModelSession(client, authorization=approval)
                if model not in session.discover():
                    raise ServiceError('NEMOTRON_NOT_DISCOVERED')
                session.ready = True
                specialists = LiveSpecialists(session)
                from uuid import uuid4
                import json
                from .execution import ExecutionTrace
                trace = ExecutionTrace()
                attempt = {'classification': 'Nightshift transport; inspect receipts, not a blanket live acceptance claim',
                           'requested_model': model, 'endpoint': endpoint, 'status': 'started'}
                try:
                    case = run_case(request, self.root/'memory', node=self.node,
                        reasoner=specialists, model_specialists=specialists, trace=trace)
                    attempt.update(status=case.status, investigation_id=case.investigation_id)
                    return self.store.save(case)
                except ServiceError as exc:
                    attempt.update(status='blocked', error_code=exc.code)
                    raise
                except (ValueError, OSError, KeyError):
                    attempt.update(status='blocked', error_code='LIVE_INVESTIGATION_VALIDATION_FAILED')
                    raise ServiceError('LIVE_INVESTIGATION_VALIDATION_FAILED') from None
                finally:
                    with session.lock:
                        session.failure = session.failure or 'MODEL_RUN_FINISHED'
                    attempt.update(model_requests=session.dispatches, reserved_requests=session.calls, requests_by_role=session.by_role,
                        decisions=[d.model_dump(mode='json') for d in trace.decisions],
                        execution_graph=[t.model_dump(mode='json') for t in trace.graph])
                    directory = self.root/'model-attempts'
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory/(uuid4().hex+'.json')).write_text(json.dumps(attempt, indent=2)+'\n')
        finally:
            self.execution_lock.release()

    def reconcile(self, identity, request):
        if not self.execution_lock.acquire(blocking=False):
            raise ValueError('INVESTIGATION_BUSY')
        try:
            parent=self.store.get(identity)
            child=revise_case(parent,request,self.root/'memory',self.store.snapshot_hash(identity),node=self.node)
            return self.store.save(child)
        finally:
            self.execution_lock.release()

    def latest(self):
        _, rows=self.store.summaries(1)
        if not rows:
            raise KeyError('NO_INVESTIGATIONS')
        return self.store.get(rows[0].investigation_id)

    def read(self, path, params):
        if set(params)-{'limit','offset','investigation_id','format'} or any(len(v)!=1 for v in params.values()):
            raise ValueError('INVALID_QUERY')
        limit=int(params.get('limit',['20'])[0])
        offset=int(params.get('offset',['0'])[0])
        if not 1<=limit<=100 or not 0<=offset<=100000:
            raise ValueError('INVALID_PAGINATION')
        now=datetime.now(timezone.utc)
        identity=params.get('investigation_id',[None])[0]
        resource=path.removeprefix('/api/v1/').rstrip('/')
        if 'format' in params and not resource.endswith('/report'):
            raise ValueError('INVALID_QUERY')
        parts=resource.split('/')
        if len(parts)>3:
            raise KeyError('ENDPOINT_NOT_FOUND')
        if parts[0]!='investigations' and resource not in ('health','medicines','hospitals','sources/status','disruptions','map/layers'):
            raise KeyError('ENDPOINT_NOT_FOUND')
        response=ApiResponse(resource=resource,investigation_id=identity,as_of=now,offset=offset,limit=limit,items=[])
        if resource=='health':
            # Read-only health never probes providers or implies a running model/poller.
            items=[Health(checked_at=now,storage='local SQLite initialized',model_runtime='not probed; live multi-agent unverified',
                zetaris='not probed; use explicit live diagnostics',network='offline read; no automatic refresh or inference')]
        elif resource=='investigations':
            total,items=self.store.summaries(limit,offset)
            response.total=total
            response.items=items
            return response
        else:
            if parts[0]=='investigations' and len(parts)>=2:
                case=self.store.get(parts[1])
            elif identity:
                case=self.store.get(identity)
            else:
                try:
                    case=self.latest()
                except KeyError:
                    response.availability='unavailable'
                    response.reason='No saved investigation. POST /api/v1/investigations to create an explicit replay case.'
                    return response
            response.investigation_id=case.investigation_id
            if parts[0]=='investigations':
                section=parts[2] if len(parts)==3 else None
                if section is None:
                    items=[case]
                elif section=='agents':
                    items=case.agents
                elif section=='execution-graph':
                    items=case.execution_graph
                elif section=='decision-ledger':
                    items=case.decision_ledger
                elif section=='conflicts':
                    items=case.conflict_resolution.conflicts if case.conflict_resolution else []
                elif section=='resolution':
                    items=[case.conflict_resolution] if case.conflict_resolution else []
                elif section=='revision':
                    items=[case.revision] if case.revision else []
                elif section=='forecast-deltas':
                    items=case.revision.forecast_deltas if case.revision else []
                elif section=='evidence':
                    items=case.evidence
                elif section=='forecast':
                    items=[ForecastSeries(case=label,forecast=f) for label,f in forecasts(case)]
                elif section=='clinical-impact':
                    items=case.clinical_impact
                elif section=='mitigations':
                    items=case.mitigation.evaluations
                elif section=='actions':
                    items=self.store.actions(case.investigation_id)
                elif section=='report':
                    fmt=params.get('format',['json'])[0]
                    if fmt=='pdf':
                        items=[]
                        response.availability='unavailable'
                        response.reason='PDF generator unavailable; JSON, CSV and Markdown briefing available.'
                    elif fmt in ('json','csv','markdown'):
                        exported=report(case)
                        items=[ReportExport(format=fmt,content=exported[fmt])]
                    else:
                        raise ValueError('INVALID_REPORT_FORMAT')
                else:
                    raise KeyError('ENDPOINT_NOT_FOUND')
            elif resource=='medicines':
                items=case.medicines
            elif resource=='hospitals':
                items=case.hospitals
            elif resource=='sources/status':
                items=[SourceHealth(status=s,evaluated_at=now,stale=(not s.last_success_at or
                    (now-s.last_success_at).total_seconds()>s.target_cadence_seconds)) for s in case.source_status]
                if not items:
                    response.availability='unavailable'
                    response.reason='No source monitoring observations in historical synthetic replay.'
            elif resource=='disruptions':
                items=[case.intelligence.signal] if case.intelligence else []
                if not items:
                    response.availability='unavailable'
                    response.reason='No observed disruption revision eligible at historical cutoff; synthetic hold remains in case forecasts.'
            elif resource=='map/layers':
                items=[]
                if case.intelligence:
                    for leg in case.intelligence.dependency.legs:
                        b=leg.geography
                        items.append(MapLayer(layer_id=leg.leg_id,bounds=[b.west,b.south,b.east,b.north],
                            source_ids=[case.dataset_version],label=leg.origin+' → '+leg.destination,
                            alert_state='conditional-exposure',classification='simulated-route',
                            limitations='Scenario bounding area; no vessel coordinates or real hospital location.'))
                    for index,b in enumerate(case.intelligence.signal.geography):
                        items.append(MapLayer(layer_id=f'advisory-{index}',bounds=[b.west,b.south,b.east,b.north],
                            source_ids=case.intelligence.signal.evidence_ids,label=case.intelligence.signal.title,
                            alert_state='advisory',classification='derived-source-geography',
                            limitations='Curated bounds from named waters; no observed event point.'))
                else:
                    response.availability='unavailable'
                    response.reason='No grounded route geometry in July synthetic network.'
            else:
                raise KeyError('ENDPOINT_NOT_FOUND')
        response.total=len(items)
        response.items=items[offset:offset+limit]
        return response

    def handle(self, method, target, body=None):
        parsed=urlsplit(target)
        if parsed.path.startswith('/api/v1/helene/'):
            from phases.phase_06_helene.service import handle
            return handle(self,method,parsed.path,body,parsed.query)
        try:
            if not parsed.path.startswith('/api/v1/'):
                raise KeyError('ENDPOINT_NOT_FOUND')
            params=parse_qs(parsed.query,keep_blank_values=True)
            if method=='GET':
                response=self.read(parsed.path,params)
                return 200,response.model_dump(mode='json')
            if parsed.query:
                raise ValueError('POST_QUERY_DENIED')
            if parsed.path=='/api/v1/investigations':
                response=self.create(CaseRequest.model_validate(body))
                return 201,response.model_dump(mode='json')
            if parsed.path=='/api/v1/chat':
                return 200,answer(self,ChatRequest.model_validate(body)).model_dump(mode='json')
            parts=parsed.path.split('/')
            if len(parts)==6 and parts[3]=='investigations' and parts[5]=='reconcile':
                response=self.reconcile(parts[4],ReconciliationRequest.model_validate({} if body is None else body))
                return 201,response.model_dump(mode='json')
            if len(parts)==8 and parts[3]=='investigations' and parts[5]=='actions' and parts[7]=='review':
                response=self.store.review(parts[4],parts[6],ActionReview.model_validate(body))
                return 200,response.model_dump(mode='json')
            raise KeyError('ENDPOINT_NOT_FOUND')
        except KeyError as exc:
            return 404,{'error':str(exc.args[0]),'schema_version':'5a.1'}
        except ValidationError:
            return 422,{'error':'REQUEST_SCHEMA_INVALID','schema_version':'5a.1'}
        except ValueError as exc:
            code=str(exc)
            safe=code if code.isupper() and len(code)<100 else 'REQUEST_INVALID'
            conflict=code in ('REVISION_CONFLICT','ACTION_TRANSITION_DENIED','INVESTIGATION_BUSY','REVIEW_ATTESTATIONS_REQUIRED')
            return 409 if conflict else 400,{'error':safe,'schema_version':'5a.1'}
