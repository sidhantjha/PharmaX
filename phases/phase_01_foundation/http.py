"""Bounded JSON transport and sanitized external-service errors."""
import json
from time import monotonic
import httpx


class ServiceError(RuntimeError):
    def __init__(self, code, *, blocked=False, diagnostics=None):
        super().__init__(code)
        self.code, self.blocked = code, blocked
        self.diagnostics = diagnostics or {}


def request_json(client, method, url, *, max_bytes=1_000_000, inspect_error=None, deadline_seconds=60,
                 on_response=None, **kwargs):
    deadline = monotonic() + deadline_seconds
    try:
        with client.stream(method, url, **kwargs) as response:
            if on_response:
                on_response(response.status_code, {k:response.headers[k] for k in (
                    'x-request-id','request-id','nvcf-reqid') if k in response.headers})
            if response.status_code >= 400:
                code = response.status_code
                diagnostics = {}
                if inspect_error:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if monotonic() > deadline:
                            raise ServiceError('TIMEOUT', blocked=True)
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            break
                    if len(body) <= max_bytes:
                        try:
                            diagnostics = inspect_error(json.loads(body))
                        except (ValueError, UnicodeError, TypeError):
                            pass
                raise ServiceError(f'HTTP_{code}', blocked=code in (401, 403, 429),
                                   diagnostics=diagnostics)
            body = bytearray()
            for chunk in response.iter_bytes():
                if monotonic() > deadline:
                    raise ServiceError('TIMEOUT', blocked=True)
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ServiceError('RESPONSE_TOO_LARGE')
            try:
                return json.loads(body), str(response.url)
            except (ValueError, UnicodeError):
                raise ServiceError('INVALID_JSON') from None
    except httpx.TimeoutException:
        raise ServiceError('TIMEOUT', blocked=True) from None
    except httpx.RequestError:
        raise ServiceError('UNREACHABLE', blocked=True) from None


def make_client():
    return httpx.Client(timeout=httpx.Timeout(20, connect=4), follow_redirects=False, trust_env=False,
                        headers={'User-Agent': 'PharmaX-Foundation/0.1'})
