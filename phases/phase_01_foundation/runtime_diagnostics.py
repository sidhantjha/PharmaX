"""Safe Nightshift-only diagnostics. GETs only; never dispatches inference."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
from time import monotonic
from urllib.parse import urlsplit
import httpx
from .config import load_env
from .http import ServiceError, request_json
from .nightshift import Nightshift, runtime_base


REMEDIATION = {
    'TIMEOUT': 'Check the explicitly configured Windows IP, listener binding and scoped firewall access from WSL. Do not rotate credentials for a TCP timeout.',
    'UNREACHABLE': 'Start Nightshift and verify its port/listener. WSL NAT loopback is separate from Windows loopback.',
    'HTTP_401': 'Select the existing runtime Bearer token from the process environment or an explicitly chosen private env file; rt-local is invalid.',
    'HTTP_403': 'Review runtime app/scope authorization for meterless-app=pharmax, meterless-scope=user.',
    'NIGHTSHIFT_TOKEN_MISSING': 'Select an existing private env file with --env-file or set METERLESS_RUNTIME_TOKEN in the process environment.',
    'NIGHTSHIFT_INVALID_CATALOG': 'Check that /v1/models is the Runtime model catalog rather than a UI/proxy page.',
    'NO_COST_ROUTE_UNCONFIRMED': 'Independently verify current account no-charge terms, runtime single-attempt behavior and disabled external/MCP tools; supply reviewed backend authorization. Catalog zero prices or PHARMAX_MODEL_NO_COST do not authorize inference.',
}


def diagnose(*, env_file=None, base_url=None, client=None):
    load_env(env_file, prefix='METERLESS_')
    # Explicit override only; no guesses, environment mutation or automatic fallback.
    base = runtime_base(base_url or os.environ.get('METERLESS_BASE_URL', 'http://127.0.0.1:8088/v1'))
    target = urlsplit(base)
    model = os.environ.get('METERLESS_MODEL', '')
    safe_model = model if re.fullmatch(r'(nvidia|local-runtime)/[a-zA-Z0-9_.-]+', model) else 'unconfigured-or-invalid'
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'endpoint': base,
              'selected_model': safe_model, 'token_present': bool(os.environ.get('METERLESS_RUNTIME_TOKEN')),
              'inference_posts': 0, 'live_tool_execution': 'unverified', 'checks': {}}
    started = monotonic()
    try:
        with socket.create_connection((target.hostname, target.port or (443 if target.scheme == 'https' else 80)), timeout=2):
            pass
        report['checks']['tcp'] = {'status': 'connected'}
    except OSError as exc:
        code = 'TIMEOUT' if isinstance(exc, TimeoutError) else 'UNREACHABLE'
        report['checks']['tcp'] = {'status': 'blocked', 'code': code, 'remediation': REMEDIATION[code]}
    report['checks']['tcp']['elapsed_seconds'] = round(monotonic() - started, 3)
    owned = client is None
    if owned:
        client = httpx.Client(timeout=httpx.Timeout(5, connect=2), trust_env=False, follow_redirects=False)
    try:
        provider = Nightshift(client, base_url=base)
        for name, operation in (
            ('health', lambda: request_json(client, 'GET', base.removesuffix('/v1') + '/healthz',
                                           max_bytes=32000, timeout=httpx.Timeout(5, connect=2), deadline_seconds=5)),
            ('catalog', provider.discover),
        ):
            started = monotonic()
            try:
                value = operation()
                if name == 'health':
                    body = value[0]
                    if not isinstance(body, dict) or body.get('status') != 'ok':
                        raise ServiceError('RUNTIME_NOT_READY')
                    row = {'status': 'reachable', 'meaning': 'process health only; not inference readiness'}
                else:
                    selected = next((m for m in provider.catalog if m['id'] == model), {})
                    metadata = selected.get('meterless')
                    price = metadata.get('price') if isinstance(metadata, dict) else None
                    # Closed projection, no arbitrary provider metadata or model IDs.
                    amounts = {k: v for k, v in price.items() if k in ('input_per_m', 'output_per_m')
                               and type(v) in (int, float) and 0 <= v < 1e9} if isinstance(price, dict) else {}
                    row = {'status': 'reachable', 'model_count': len(value), 'selected_present': model in value,
                           'catalog_price_per_million': amounts, 'cost_authorization': 'unconfirmed'}
                report['checks'][name] = row
            except ServiceError as exc:
                report['checks'][name] = {'status': 'blocked', 'code': exc.code,
                    'remediation': REMEDIATION.get(exc.code, 'Check the Runtime HTTP route/schema and service readiness; no inference was sent.')}
            report['checks'][name]['elapsed_seconds'] = round(monotonic() - started, 3)
    finally:
        if owned:
            client.close()
    report['inference_authorization'] = {'status': 'not-authorized', 'code': 'NO_COST_ROUTE_UNCONFIRMED',
                                         'remediation': REMEDIATION['NO_COST_ROUTE_UNCONFIRMED']}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--base-url', help='Explicit known Runtime endpoint; never automatically discovered')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = diagnose(env_file=args.env_file, base_url=args.base_url)
    except (ServiceError, ValueError, OSError):
        report = {'error': 'RUNTIME_CONFIGURATION_INVALID', 'inference_posts': 0,
                  'remediation': 'Check the selected env file and endpoint. Never include credentials in the URL.'}
    rendered = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end='')
    return 1 if 'error' in report else 0


if __name__ == '__main__':
    raise SystemExit(main())
