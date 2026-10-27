"""Explicit synthetic policy proof for mocked provider tests, never live approval."""
from datetime import datetime, timedelta, timezone
import os
import pytest
from phases.phase_01_foundation.inference_policy import InferenceAuthorization
from phases.phase_01_foundation.nightshift import runtime_base


@pytest.fixture
def mock_authorization():
    # Late binding: individual tests configure their model/endpoint after fixtures.
    class MockAuthorization:
        def require(self, endpoint, model):
            InferenceAuthorization(
                endpoint=runtime_base(os.environ.get('METERLESS_BASE_URL', 'http://127.0.0.1:8088/v1')),
                model=os.environ['METERLESS_MODEL'], provider='nvidia',
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                evidence_refs=('mock://synthetic-policy-proof-not-valid-for-live-use',),
                no_charge_confirmed=True, runtime_single_attempt=True,
                runtime_external_tools_disabled=True).require(endpoint, model)
    return MockAuthorization()
