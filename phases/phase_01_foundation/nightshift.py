"""Nightshift provider only. No direct NVIDIA fallback or downloads; single attempts and reviewed cost authorization.

Protocol: https://www.meterless.ai/engines.html (Runtime provider contract v4).
Reuse the legacy adapter's bounded catalog/strict schema approach and safe traces.
"""
import os
import re
import httpx
from ipaddress import IPv4Address, IPv4Network
from urllib.parse import urlsplit
from contracts import ToolRequest
from .http import ServiceError, request_json
from .zetaris import safe_origin
from .native_calls import parse_call, validate_identity


def runtime_base(value):
    if '://' not in value:
        value = 'http://' + value
    p = urlsplit(value)
    # Explicit Windows LAN configuration only; do not weaken other service policies.
    try:
        lan = any(IPv4Address(p.hostname or '') in IPv4Network(net)
                  for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10'))
    except ValueError:
        lan = False
    if p.scheme == 'http' and lan and not p.username and not p.password:
        origin = f'{p.scheme}://{p.netloc}'
    else:
        origin = safe_origin(f'{p.scheme}://{p.netloc}')
    if p.query or p.fragment or p.path.rstrip('/') not in (
            '', '/v1', '/v1/models', '/v1/chat/completions', '/healthz'):
        raise ServiceError('INVALID_RUNTIME_URL')
    return origin + '/v1'


def error_diagnostics(body):
    """Closed projection: never return arbitrary provider text or private reasoning."""
    if not isinstance(body, dict) or not isinstance(body.get('error'), dict):
        return {}
    error = body['error']
    result = {}
    if error.get('code') in ('provider_route_failed', 'provider_error', 'first_token_timeout',
                             'engine_timeout', 'model_not_on_app_lanes', 'model_blocked_by_app_mode'):
        result['runtime_code'] = error['code']
    detail = error.get('meterless_detail', '')
    if isinstance(detail, str):
        signals = [s for s in ('race_failed', 'nvidia_key_missing', 'fetch failed',
                              'ttfb_timeout', 'provider_deadline', 'Failed to fetch') if s in detail]
        signals += re.findall(r'\bnvidia_(?:400|401|402|403|404|429|500|502|503|504)\b', detail)
        if signals:
            result['signals'] = sorted(set(signals))
        # A 429 alone cannot distinguish a per-minute cap from exhausted quota.
        # Retain only a closed classification, never the upstream body/detail.
        if 'nvidia_402' in signals:
            result['provider_failure'] = 'billing-or-credit-required'
        elif 'nvidia_429' in signals:
            lower = detail.lower()
            if any(s in lower for s in ('insufficient_quota', 'insufficient credit', 'out of credits',
                                        'credits exhausted', 'quota exhausted', 'payment required')):
                result['provider_failure'] = 'credit-or-account-quota-exhausted'
            elif any(s in lower for s in ('rate limit', 'rate_limit', 'too many requests', 'per-minute')):
                result['provider_failure'] = 'rate-limited'
            else:
                result['provider_failure'] = 'rate-limit-or-quota-unconfirmed'
        elif 'nvidia_401' in signals:
            result['provider_failure'] = 'authentication-rejected'
        elif 'nvidia_403' in signals:
            result['provider_failure'] = 'provider-access-forbidden'
    return result


class Nightshift:
    def __init__(self, client, *, authorization=None, base_url=None):
        self.client = client
        self.authorization = authorization
        self.base = runtime_base(base_url or os.environ.get('METERLESS_BASE_URL', 'http://127.0.0.1:8088/v1'))
        self.token = os.environ.get('METERLESS_RUNTIME_TOKEN', '')
        self.model = os.environ.get('METERLESS_MODEL', '')
        self.calls = 0
        self.response_model = None
        self.response_provider = None
        self.diagnostics = []

    def authorize(self, *, allow_paid=False):
        # Explicit legacy paid permission is separate from a claim of zero cost.
        if allow_paid is not True:
            if self.authorization is None:
                raise ServiceError('NO_COST_ROUTE_UNCONFIRMED', blocked=True)
            self.authorization.require(self.base, self.model)

    def headers(self):
        if not self.token or self.token == 'rt-local':
            raise ServiceError('NIGHTSHIFT_TOKEN_MISSING', blocked=True)
        return {'Authorization': 'Bearer ' + self.token, 'meterless-app': 'pharmax',
                'meterless-scope': 'user'}

    def discover(self):
        result, _ = request_json(self.client, 'GET', self.base + '/models',
                                 max_bytes=1_000_000, timeout=httpx.Timeout(5, connect=2), deadline_seconds=5, headers=self.headers())
        data = result.get('data') if isinstance(result, dict) else None
        if not isinstance(data, list) or len(data) > 2000 or any(
                not isinstance(m, dict) or not isinstance(m.get('id'), str) for m in data):
            raise ServiceError('NIGHTSHIFT_INVALID_CATALOG')
        self.catalog = data
        return sorted({m['id'] for m in data})

    def smoke(self, model_ids):
        # Reserve before transport. One inference maximum, including failures.
        if self.calls >= 1:
            raise ServiceError('MODEL_CALL_BUDGET')
        self.authorize()
        if self.model not in model_ids or 'nemotron' not in self.model.lower():
            raise ServiceError('NEMOTRON_NOT_DISCOVERED', blocked=True)
        self.calls += 1
        schema = ToolRequest.model_json_schema()
        schema['required'] = list(schema['properties'])
        for prop in schema['properties'].values():
            prop.pop('default', None)
        result, _ = request_json(self.client, 'POST', self.base + '/chat/completions',
            max_bytes=32_000, timeout=httpx.Timeout(30, connect=3), deadline_seconds=30, headers={**self.headers(), 'meterless-tier-split': 'off'}, json={
                'model': self.model, 'temperature': 0, 'max_tokens': 160,
                **({'max_price': 0} if self.authorization else {}),
                'messages': [{'role': 'user', 'content':
                    'Return one approved read request as JSON. request_id=smoke, '
                    'name=zetaris.connection_check, limit=1. No other action.'}],
                'response_format': {'type': 'json_schema', 'json_schema': {
                    'name': 'approved_read', 'strict': True,
                    'schema': schema}}})
        validate_identity(result, self.model)
        try:
            if len(result['choices']) != 1:
                raise ValueError
            request = ToolRequest.model_validate_json(result['choices'][0]['message']['content'])
        except (KeyError, IndexError, TypeError, ValueError):
            raise ServiceError('MODEL_SCHEMA_INVALID') from None
        if request.name != 'zetaris.connection_check' or request.limit != 1 or request.request_id != 'smoke':
            raise ServiceError('MODEL_TOOL_SCOPE_DENIED')
        usage = result.get('usage', {})
        tokens = {k: v for k, v in usage.items() if k in (
            'prompt_tokens', 'completion_tokens', 'total_tokens') and type(v) is int and v >= 0
        } if isinstance(usage, dict) else {}
        return request, tokens


    def read(self, model_ids, *, allow_paid=False, on_dispatch=None, max_attempts=1, on_error=None):
        """One native read. Historical max_attempts=2 is accepted but never retried."""
        if type(max_attempts) is not int or max_attempts not in (1, 2):
            raise ValueError('max_attempts must be 1 or 2')
        if self.calls >= 1:
            raise ServiceError('MODEL_CALL_BUDGET')
        self.authorize(allow_paid=allow_paid)
        if self.model not in model_ids or 'nemotron' not in self.model.lower():
            raise ServiceError('NEMOTRON_NOT_DISCOVERED', blocked=True)
        headers = {**self.headers(), 'meterless-tier-split': 'off'}
        schema = ToolRequest.model_json_schema()
        schema['required'] = list(schema['properties'])
        for prop in schema['properties'].values():
            prop.pop('default', None)
        schema['properties']['name']['enum'] = ['fda.sample', 'zetaris.connection_check']
        schema['properties']['request_id']['const'] = 'model-read'
        schema['properties']['limit']['const'] = 1
        payload = {
                'model': self.model, 'temperature': 0, 'max_tokens': 160,
                **({'max_price': 0} if self.authorization else {}),
                'messages': [{'role': 'user', 'content':
                    'Retrieve one current FDA shortage source record using pharmax_read. '
                    'Use request_id=model-read, name=fda.sample, limit=1. '
                    'Do not invent evidence or write an answer before retrieval.'}],
                'tools': [{'type': 'function', 'function': {
                    'name': 'pharmax_read', 'description': 'Execute an approved bounded read only.',
                    'parameters': schema}}],
                'tool_choice': {'type': 'function', 'function': {'name': 'pharmax_read'}},
                'parallel_tool_calls': False,
                'chat_template_kwargs': {'enable_thinking': False}}
        self.calls += 1
        if on_dispatch:
            on_dispatch()
        try:
            result, _ = request_json(self.client, 'POST', self.base + '/chat/completions',
                max_bytes=32_000, headers=headers, timeout=httpx.Timeout(30, connect=3), deadline_seconds=30, json=payload,
                inspect_error=error_diagnostics)
        except ServiceError as exc:
            self.diagnostics.append({'attempt': self.calls, 'error': exc.code, **exc.diagnostics})
            if on_error:
                on_error(exc, self.calls, False)
            raise
        function, arguments, tokens = parse_call(result, self.model, {'pharmax_read'})
        self.response_model = result['model']
        self.response_provider = 'nvidia'
        try:
            request = ToolRequest.model_validate(arguments)
        except ValueError:
            raise ServiceError('MODEL_TOOL_CALL_INVALID') from None
        if (request.name not in ('fda.sample', 'zetaris.connection_check')
                or request.limit != 1 or request.request_id != 'model-read'):
            raise ServiceError('MODEL_TOOL_SCOPE_DENIED')
        return request, tokens
