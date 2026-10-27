"""Read-only contract services plus optional loopback HTTP; never fetches or investigates."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit, parse_qs
from .registry import lifecycle

PATHS=('signals','sources','citations','matches','risk','forecasts','activity','mitigations')


class IntelligenceService:
    def __init__(self, registry):
        self.registry=registry

    def read(self, resource, *, cutoff=None, limit=20):
        if resource not in PATHS or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Read resource/limit denied')
        now=datetime.now(timezone.utc)
        as_of=cutoff or now
        if as_of.tzinfo is None:
            raise ValueError('Aware cutoff required')
        signals=self.registry.latest(as_of,limit=limit)
        runs=[r for r in self.registry.runs() if datetime.fromisoformat(r['decision_at'].replace('Z','+00:00'))<=as_of]
        latest=runs[0] if runs else None
        decision_state='none'
        if latest:
            current=next((s for s in signals if s.event_id==latest['signal']['event_id']),None)
            decision_state=('current-for-retained-revision' if current and current.revision_id==latest['signal']['revision_id']
                and lifecycle(current,as_of,signals)=='active' else 'stale-revision-or-expired')
        if resource=='signals':
            items=[{'signal':s.model_dump(mode='json'),'effective_status':lifecycle(s,as_of,signals)} for s in signals]
        elif resource=='sources':
            items=[]
            for s in self.registry.statuses():
                if s.last_attempt_at and s.last_attempt_at>as_of:
                    items.append({'source':s.source,'history_available':False,'monitoring':'last-check-only'})
                else:
                    items.append({**s.model_dump(mode='json'),'stale':not s.last_success_at or
                        (as_of-s.last_success_at).total_seconds()>s.target_cadence_seconds})
        elif resource=='citations':
            items=[e.model_dump(mode='json') for s in signals for e in s.evidence]
        elif resource=='matches':
            items=[{'match':r['match'],'dependency':r['dependency']} for r in runs[:limit]]
        elif resource=='risk':
            items=[] if not latest else [{'run_id':latest['run_id'],'baseline':latest['baseline'],
                'sensitivity':latest['sensitivity'],'selected_delay_days':latest['selected_delay_days'],
                'signal_revision':latest['signal']['revision_id'],'impact_label':'calculated-from-simulated-assumptions'}]
        elif resource=='activity':
            items=[] if not latest else latest['events']
        elif resource=='mitigations':
            items=[] if not latest or not latest['mitigation'] else [latest['mitigation']]
        else:
            items=[]
            if latest and latest['baseline']:
                items.append({'case':'scheduled','points':latest['baseline']['forecast']['points']})
                items.extend({'case':f'delay-{s["delay_days"]}','points':s['assessment']['forecast']['points']}
                             for s in latest['sensitivity'])
                mitigation=latest.get('mitigation')
                if mitigation:
                    for evaluation in mitigation['evaluations']:
                        for impact in evaluation['impacts']:
                            if impact['projected']:
                                items.append({'case':evaluation['candidate']['candidate_id'],
                                    'hospital_id':impact['hospital_id'],'feasible':evaluation['feasible'],
                                    'diagnostic_only':not evaluation['feasible'],
                                    'points':impact['projected']['forecast']['points']})
        return {'schema_version':'4c.1','as_of':as_of.isoformat(),'historical':cutoff is not None,
            'latest_decision_state':decision_state,
            'monitoring':'Last-check records only. Continuous polling requires the explicit local --poll process.',
            'items':items}


def serve(registry, port=8766, *, operator=None):
    service=IntelligenceService(registry)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def operator_request(self, method):
            if not operator or not urlsplit(self.path).path.startswith('/api/v1/'):
                return False
            body=None
            if method=='POST':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    budget = 4*1024*1024 if self.path == '/api/v1/helene/operations/import' else 16384
                    if not 1<=length<=budget or self.headers.get('Transfer-Encoding'):
                        raise ValueError
                    body=json.loads(self.rfile.read(length))
                except (ValueError,TypeError):
                    payload=b'{"schema_version":"5a.1","error":"REQUEST_JSON_INVALID"}'
                    self.send_response(400)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return True
            try:
                status,result=operator.handle(method,self.path,body)
                payload=json.dumps(result).encode()
                if len(payload)>16_000_000:
                    status,payload=413,b'{"schema_version":"5a.1","error":"RESPONSE_BUDGET"}'
            except Exception:
                # Never send exception text, provider bodies or credentials over HTTP.
                status,payload=500,b'{"schema_version":"5a.1","error":"OPERATOR_SERVICE_FAILED"}'
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return True

        def do_GET(self):
            if self.operator_request('GET'):
                return
            try:
                parsed=urlsplit(self.path)
                prefix='/v1/intelligence/'
                if not parsed.path.startswith(prefix):
                    raise ValueError
                params=parse_qs(parsed.query)
                if set(params)-{'limit','as_of'} or any(len(v)!=1 for v in params.values()):
                    raise ValueError
                cutoff=datetime.fromisoformat(params['as_of'][0].replace('Z','+00:00')) if 'as_of' in params else None
                body=json.dumps(service.read(parsed.path[len(prefix):],cutoff=cutoff,
                    limit=int(params.get('limit',['20'])[0]))).encode()
                if len(body)>8_000_000:
                    raise ValueError
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ValueError,TypeError):
                self.send_error(400,'Read request denied')

        def do_POST(self):
            if self.operator_request('POST'):
                return
            self.send_error(405,'Read-only service')
    httpd=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'Loopback intelligence API http://127.0.0.1:{httpd.server_port}/v1/intelligence/signals; no poller or model started.',flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
