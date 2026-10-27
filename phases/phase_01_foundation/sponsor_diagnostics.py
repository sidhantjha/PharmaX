"""Explicit credential-file, GET-only sponsor transport checks. Never inference.

Provider keys, prompts, reasoning and arbitrary receipt fields are not retained.
The runtime token is read in place from the caller's explicitly selected existing
proxy-token file. This module does not inspect Nightshift's provider key store.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from time import monotonic
import httpx
from .http import request_json, ServiceError
from .nightshift import runtime_base
from .zetaris import Zetaris, APPROVED_READS

MODEL = 'nvidia/nemotron-3.5-lightning-30b-a3b'


def diagnose(base_url, *, token_file=None, client=None):
    base = runtime_base(base_url)
    token = os.environ.get('METERLESS_RUNTIME_TOKEN', '')
    if not token and token_file:
        selected = Path(token_file)
        if selected.name != 'proxy-token' or not selected.is_file() or selected.stat().st_size > 4096:
            raise ValueError('SELECT_EXISTING_RUNTIME_PROXY_TOKEN')
        token = selected.read_text().strip()
    report = dict(schema_version='sponsor-check-1', checked_at=datetime.now(timezone.utc).isoformat(),
        endpoint=base, selected_model=MODEL, runtime_token_present=bool(token),
        inference_posts=0, provider_key_store_read=False, checks={}, live_inference='unverified',
        account_entitlement='unverified; active keys and rate limits are not no-charge evidence')
    owned = client is None
    client = client or httpx.Client(trust_env=False, follow_redirects=False,
                                   timeout=httpx.Timeout(5, connect=2))
    headers = {'Authorization':'Bearer '+token, 'meterless-app':'pharmax', 'meterless-scope':'user'}
    checks = [('health', base.removesuffix('/v1')+'/healthz', False, 32000),
              ('unauthenticated_catalog', base+'/models', False, 1_000_000)]
    if token:
        checks += [('authenticated_catalog', base+'/models', True, 1_000_000)]
    try:
        for name, url, authenticated, limit in checks:
            started = monotonic()
            try:
                body, _ = request_json(client, 'GET', url, headers=headers if authenticated else {},
                    max_bytes=limit, timeout=httpx.Timeout(5, connect=2), deadline_seconds=5)
                if not isinstance(body, dict):
                    raise ServiceError('UNEXPECTED_RESPONSE_SCHEMA')
                if name == 'health':
                    row = dict(status='reachable' if body.get('status')=='ok' else 'unexpected-schema',
                               meaning='process health only')
                else:
                    data = body.get('data')
                    if not isinstance(data, list) or len(data)>2000:
                        raise ServiceError('NIGHTSHIFT_INVALID_CATALOG')
                    selected = next((m for m in data if isinstance(m,dict) and m.get('id')==MODEL), {})
                    metadata = selected.get('meterless', {})
                    price = metadata.get('price', {}) if isinstance(metadata,dict) else {}
                    if not isinstance(price, dict):
                        raise ServiceError('NIGHTSHIFT_INVALID_CATALOG')
                    row = dict(status='reachable', model_count=len(data), selected_present=bool(selected),
                        catalog_price_per_million={k:v for k,v in price.items()
                            if k in {'input_per_m','output_per_m'} and type(v) in (int,float) and 0<=v<1e9})
                report['checks'][name] = row
            except ServiceError as exc:
                # Closed error codes only: no exception/provider body or headers.
                code = exc.code if re.fullmatch(r'[A-Z0-9_]{1,80}', exc.code) else 'CHECK_FAILED'
                report['checks'][name] = dict(status='blocked', code=code)
            report['checks'][name]['elapsed_seconds'] = round(monotonic()-started,3)
    finally:
        if owned:
            client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--runtime-token-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check-zetaris', action='store_true', help='Also attempt the approved read-only connection check')
    args = parser.parse_args()
    try:
        report = diagnose(args.base_url, token_file=args.runtime_token_file)
        if args.check_zetaris:
            dispatched = []
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(5,connect=2),
                              event_hooks={'request':[lambda req:dispatched.append(req.method)]}) as client:
                try:
                    rows = Zetaris(client).query('connection_check',limit=1)
                    report['zetaris'] = dict(status='connection-query-verified', rows=rows,
                        result_schema='one column / one row; constant integer',
                        limitation='SELECT 1 proves connectivity, not a functioning PharmaX data layer.')
                except ServiceError as exc:
                    report['zetaris'] = dict(status='blocked', code=exc.code)
            report['zetaris'].update(query=APPROVED_READS['connection_check'], http_requests=len(dispatched),
                environment_presence={key:bool(os.environ.get(key)) for key in (
                    'ZETARIS_BASE_URL','ZETARIS_ORG_ID','ZETARIS_API_KEY','ZETARIS_USERNAME','ZETARIS_PASSWORD')})
    except (OSError, ValueError, ServiceError):
        report = dict(error='SPONSOR_DIAGNOSTIC_CONFIGURATION_INVALID', inference_posts=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
