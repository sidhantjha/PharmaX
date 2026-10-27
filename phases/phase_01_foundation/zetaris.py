"""Adapted from the installed Zetaris getting-started connector.

Uses the existing UI proxy login, organization header and run-query route.
Only registered, bounded read queries may execute. No municipal tables touched.
"""
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4
from .http import ServiceError, request_json

APPROVED_READS = {'connection_check': 'SELECT 1 AS pharmax_connection_check'}


def safe_origin(value):
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ServiceError('INVALID_SERVICE_ORIGIN')
    if p.path not in ('', '/') or p.query or p.fragment:
        raise ServiceError('INVALID_SERVICE_ORIGIN')
    if p.scheme != 'https' and p.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ServiceError('REMOTE_SERVICE_REQUIRES_TLS')
    return value.rstrip('/')


class Zetaris:
    def __init__(self, client):
        self.client = client
        self.base = safe_origin(os.environ.get('ZETARIS_BASE_URL', 'http://localhost:3000'))
        self.org = os.environ.get('ZETARIS_ORG_ID', '')

    def query(self, name='connection_check', *, limit=1):
        if name not in APPROVED_READS or type(limit) is not int or not 1 <= limit <= 5:
            raise ServiceError('QUERY_NOT_APPROVED')
        rows = self.submit_sql(APPROVED_READS[name], limit=limit)['data']
        if name == 'connection_check' and rows not in ([[1]], [['1']]):
            raise ServiceError('ZETARIS_CHECK_MISMATCH')
        return rows

    def submit_sql(self, sql, *, limit):
        """Trusted infrastructure transport, not a tool API. Call reviewed compilers only.

        Phase 1's model-facing query() remains restricted to APPROVED_READS.
        Phase 2 produces immutable registration and bounded SELECT plans internally.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError('ZETARIS_ROW_LIMIT')
        if not isinstance(sql, str) or len(sql.encode()) > 1_000_000:
            raise ServiceError('ZETARIS_SQL_BUDGET')
        if not re.fullmatch(r'\d+', self.org):
            raise ServiceError('ZETARIS_ORG_MISSING', blocked=True)
        token = os.environ.get('ZETARIS_API_KEY')
        if not token:
            user, password = os.environ.get('ZETARIS_USERNAME'), os.environ.get('ZETARIS_PASSWORD')
            if not user or not password:
                raise ServiceError('ZETARIS_AUTH_MISSING', blocked=True)
            login, _ = request_json(self.client, 'POST', self.base + '/api/auth/login',
                                    json={'username': user, 'password': password})
            if not isinstance(login, dict):
                raise ServiceError('ZETARIS_INVALID_LOGIN')
            token = login.get('idToken') or login.get('token') or login.get('accessToken')
            if not isinstance(token, str) or not token:
                raise ServiceError('ZETARIS_TOKEN_MISSING')
        body = {'sql': sql, 'limit': limit}
        if os.environ.get('ZETARIS_ENGINE_ID'):
            body['engineId'] = os.environ['ZETARIS_ENGINE_ID']
        result, _ = request_json(self.client, 'POST',
            self.base + '/api/proxy/sql-editor/sqls/run-query', json=body,
            headers={'Authorization': 'Bearer ' + token, 'X-Org-ID': self.org,
                     'X-Request-ID': str(uuid4())})
        if not isinstance(result, dict) or not isinstance(result.get('data'), list):
            raise ServiceError('ZETARIS_INVALID_RESPONSE')
        if len(result['data']) > limit:
            raise ServiceError('ZETARIS_ROW_LIMIT')
        return result
