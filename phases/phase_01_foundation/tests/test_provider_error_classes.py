"""Closed runtime error classifications; no provider requests."""
import json
import pytest


@pytest.mark.parametrize('detail,expected', [
    ('nvidia_429: rate limit exceeded', 'rate-limited'),
    ('nvidia_429: insufficient_quota', 'credit-or-account-quota-exhausted'),
    ('nvidia_429: PRIVATE', 'rate-limit-or-quota-unconfirmed'),
    ('nvidia_402: PRIVATE', 'billing-or-credit-required'),
    ('nvidia_401: PRIVATE', 'authentication-rejected'),
    ('nvidia_403: PRIVATE', 'provider-access-forbidden'),
])
def test_closed_provider_diagnostics_distinguish_rate_limits_from_billing(detail, expected):
    from phases.phase_01_foundation.nightshift import error_diagnostics
    result = error_diagnostics({'error': {'code': 'provider_error', 'meterless_detail': detail}})
    assert result['provider_failure'] == expected and 'PRIVATE' not in json.dumps(result)
