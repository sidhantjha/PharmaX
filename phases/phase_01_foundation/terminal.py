"""PharmaX diagnostic terminal. Fixture mode never creates a network client."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
from contracts import CheckResult, DiagnosticReport, ToolRequest
from .config import load_env
from .http import ServiceError, make_client
from .nightshift import Nightshift
from .runtime import JSONLTrace, Runtime, save_report
from .sources import fetch_comtrade, fetch_fda, normalize_comtrade, normalize_fda
from .zetaris import Zetaris

ROOT = Path(__file__).resolve().parents[2]


def repository_status():
    def git(*args):
        try:
            result = subprocess.run(['git', '--no-optional-locks', *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
            return result.stdout.strip() if result.returncode == 0 else 'unavailable'
        except (OSError, subprocess.TimeoutExpired):
            return 'unavailable'
    status = git('status', '--porcelain')
    return {'branch': git('branch', '--show-current'), 'commit': git('rev-parse', 'HEAD'),
            'dirty': bool(status), 'status_available': status != 'unavailable', 'python': platform.python_version(),
            'virtualenv': sys.prefix != sys.base_prefix}


def fixture_handlers():
    body = json.loads((ROOT / 'phases/phase_01_foundation/fixtures/sources.json').read_text())
    observed = datetime.fromisoformat(body['observed_at'])
    return {
        'fda.sample': lambda limit: normalize_fda(body['fda'][:limit], body['fda_url'], observed, fixture=True),
        'comtrade.sample': lambda limit: normalize_comtrade(
            body['comtrade'][:limit], body['comtrade_url'], observed, fixture=True)}


def diagnose(*, offline=False, model_smoke=False, allow_paid_model=False, model_attempts=1, output_dir=Path('.runtime/pharmax')):
    if type(model_attempts) is not int or model_attempts not in (1, 2):
        raise ValueError('model_attempts must be 1 or 2')
    if offline and (model_smoke or allow_paid_model or model_attempts != 1):
        raise ValueError('Model activity cannot run offline')
    model_records = []
    mode = 'fixture' if offline else 'live'
    repo = repository_status()
    checks = [CheckResult(name='repository', status='PASS', detail=
                         f"{repo['branch']} @ {repo['commit'][:12]}; dirty={repo['dirty']}; "
                         f"Python {repo['python']}; virtualenv={repo['virtualenv']}")]
    client = None
    try:
        if offline:
            handlers = fixture_handlers()
        else:
            client = make_client()
            handlers = {'zetaris.connection_check': lambda limit: Zetaris(client).query(limit=limit),
                        'fda.sample': lambda limit: fetch_fda(client, limit, raw_dir=Path(output_dir) / 'raw'),
                        'comtrade.sample': lambda limit: fetch_comtrade(client, limit, raw_dir=Path(output_dir) / 'raw')}
        rt = Runtime(handlers, mode=mode, max_calls=4,
                     trace=JSONLTrace(Path(output_dir) / 'events.jsonl'))
        if offline:
            checks.append(CheckResult(name='zetaris', status='BLOCKED', detail='Offline fixture mode; no connection attempted.'))
        else:
            result, rows = rt.execute(ToolRequest(request_id='zetaris', name='zetaris.connection_check', limit=1))
            checks.append(CheckResult(name='zetaris', status=result.status, record_count=result.record_count,
                detail=result.error_code or f'Authenticated SELECT 1 returned {rows!r} through existing UI proxy; source=' + Zetaris(client).base + '/api/proxy/sql-editor/sqls/run-query'))
        if offline:
            checks.append(CheckResult(name='nightshift/nemotron', status='BLOCKED', detail='Offline fixture mode; no model activity.'))
        else:
            try:
                provider = Nightshift(client)
                ids = provider.discover()
                nemotron = [m for m in ids if 'nemotron' in m.lower()]
                checks.append(CheckResult(name='nightshift', status='PASS', detail=f'Authenticated catalog: {len(ids)} IDs.'))
                checks.append(CheckResult(name='nemotron', status='PASS' if nemotron else 'BLOCKED',
                    detail=f'Explicit Nemotron IDs: {nemotron}; generic routing modes do not prove Nemotron availability.'))
                rt.event('nightshift.catalog', 'PASS')
                if model_smoke:
                    def attempt_error(exc, attempt, retry):
                        rt.event(f'nightshift.attempt.{attempt}', 'FAIL', model_id=provider.model,
                                 error_code=exc.code)
                        checks.append(CheckResult(name=f'nightshift attempt {attempt}',
                            status='FAIL', detail=f'{exc.code}; diagnostics={exc.diagnostics}; '
                            f'retry={retry}; backoff_seconds={1 if retry else 0}'))
                    request, tokens = provider.read(ids, allow_paid=allow_paid_model,
                        max_attempts=model_attempts, on_error=attempt_error,
                        on_dispatch=lambda: rt.event('nightshift.inference.started', 'PASS',
                                                     model_id=provider.model))
                    rt.event('nightshift.tool_call', 'PASS', model_id=provider.response_model,
                             tokens=tokens, request_id=request.request_id)
                    result, model_records = rt.execute(request)
                    checks.append(CheckResult(name='model/tool smoke', status=result.status,
                        record_count=result.record_count, evidence_ids=result.evidence_ids,
                        detail=f'Response model={provider.response_model}; '
                        f'provider={provider.response_provider or "unverified"}; '
                        f'tool={request.name} limit={request.limit}; ' +
                        (result.error_code or 'native tool call executed; result traced.') + ' ' +
                        ('Measured token usage returned.' if tokens else 'Token usage unknown.')))
                else:
                    checks.append(CheckResult(name='model/tool smoke', status='BLOCKED', detail='No inference requested; use --model-smoke with a confirmed no-cost route or explicit --allow-paid-model authorization.'))
            except ServiceError as exc:
                status = 'BLOCKED' if exc.blocked else 'FAIL'
                checks.append(CheckResult(name='nightshift/nemotron', status=status, detail=exc.code))
                rt.event('nightshift', status, error_code=exc.code)
        checks.append(CheckResult(name='meterless engine', status='BLOCKED', detail=
            'Archived H-MEM reference requires Node 22.22+. Engine bridge not ported or validated in PharmaX.'))
        all_records = {}
        for source in ('fda', 'comtrade'):
            result, records = rt.execute(ToolRequest(request_id=source, name=source + '.sample'))
            all_records[source] = records
            detail = result.error_code or f'{len(records)} records ({mode}).'
            if source == 'comtrade':
                detail += ' HS 30; US reporter 842; world partner 0; imports; 2023; aggregate USD, not shipments.'
            checks.append(CheckResult(name=source, status=result.status, detail=detail,
                                      record_count=len(records), evidence_ids=result.evidence_ids))
        report = DiagnosticReport(checked_at=datetime.now(timezone.utc), mode=mode,
            checks=checks, shortages=[*model_records, *all_records['fda']] if model_records and
            hasattr(model_records[0], 'evidence') else all_records['fda'], trade_flows=all_records['comtrade'],
            events=rt.events, repository=repo)
        save_report(report, Path(output_dir) / 'report.json')
        return report
    finally:
        if client:
            client.close()


def render(report):
    lines = [f'PharmaX | {report.mode.upper()} | {report.checked_at.isoformat()}']
    for check in report.checks:
        lines.append(f'[{check.status:7}] {check.name}: {check.detail}')
    for record in [*report.shortages, *report.trade_flows]:
        ev = record.evidence
        if hasattr(record, 'generic_name'):
            lines.append('  Retrieved FDA facts (backend-formatted): ' + json.dumps({
                'generic_name': record.generic_name, 'status': record.status,
                'availability': record.availability, 'package_ndc': record.package_ndc}, ensure_ascii=True))
        lines.append(f'  evidence={ev.evidence_id} kind={ev.kind}\n'
                     f'  source={ev.source_url} observed={ev.observed_at.isoformat()} '
                     f'published={ev.published_on} updated={ev.updated_on}')
    for ev in report.events:
        lines.append(f'  event {ev.mode} {ev.operation} {ev.status} {ev.duration_ms}ms '
                     f'request={ev.request_id} model={ev.model_id} tokens={ev.tokens} '
                     f'evidence={ev.evidence_ids} error={ev.error_code}')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--env-file', help='Explicit private environment file, read in place; never copied.')
    parser.add_argument('--zetaris-env-file', help='Explicit private file; load only missing ZETARIS_* settings.')
    parser.add_argument('--model-attempts', type=int, choices=(1, 2), default=1,
                        help='Deprecated compatibility option: both values permit only one POST; no retries.')
    parser.add_argument('--model-smoke', action='store_true', help='Bounded model call; requires confirmed no-cost route or --allow-paid-model.')
    parser.add_argument('--allow-paid-model', action='store_true',
                        help='Authorize the bounded inference attempt budget even if cost is unknown.')
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.runtime/pharmax')
    args = parser.parse_args()
    if args.model_attempts != 1 and not args.model_smoke:
        parser.error('--model-attempts requires --model-smoke')
    if args.allow_paid_model and not args.model_smoke:
        parser.error('--allow-paid-model requires --model-smoke')
    if args.offline and args.model_smoke:
        parser.error('--model-smoke cannot run offline')
    if not args.offline:
        load_env(args.env_file)
        load_env(args.zetaris_env_file, prefix='ZETARIS_')
    report = diagnose(offline=args.offline, model_smoke=args.model_smoke,
                      allow_paid_model=args.allow_paid_model, model_attempts=args.model_attempts, output_dir=args.output_dir)
    print(render(report))
    # Blocked dependencies are nonzero; fixture-only demonstration succeeds without live services.
    return 1 if any(c.status == 'FAIL' for c in report.checks) else (
        2 if not args.offline and any(c.status == 'BLOCKED' for c in report.checks) else 0)
