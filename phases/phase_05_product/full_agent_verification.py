"""Fail-closed twelve-role preflight, NOT a model executor or live receipt generator.

The current runtime/account prerequisites are unresolved. Discover the existing
role IDs and record non-attempts transparently; do not schedule a fake agent run.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4
from .integration import RESPONSIBILITIES
from .live_specialists import PROMPTS
from .service import OperatorService
from phases.phase_08_operations.specialists import OperationalTools

MODEL = 'nvidia/nemotron-3.5-lightning-30b-a3b'
BLOCKERS = (
    'ACCOUNT_NO_CHARGE_UNVERIFIED',
    'RUNTIME_RETRIES_EXCEED_ZERO',
    'REQUEST_SCOPED_EXTERNAL_TOOL_DISABLE_UNVERIFIED',
    'HELENE_LIVE_ENTRY_POINT_UNSUPPORTED',
    'TWELVE_ROLE_NATIVE_MODEL_BINDINGS_MISSING',
    'REQUESTED_AGGREGATE_BUDGET_CONTROLLER_NOT_IMPLEMENTED',
)
REQUESTED_LIMITS = dict(specialists=12, model_requests=24, requests_per_specialist=2,
    generated_tokens_per_request=1024, aggregate_input_output_tokens=30000,
    inference_wall_clock_seconds=240, concurrent_requests=2, automatic_retries=0,
    investigations=1, tool_selections_per_specialist=1, continuations_per_specialist=1)


def record_preflight(case, output):
    started = monotonic()
    roles = list(RESPONSIBILITIES)
    if len(roles) != 12:
        raise ValueError('REPOSITORY_ROLE_COUNT_CHANGED')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output/'summary.json').exists():
        raise ValueError('PRESERVE_EXISTING_PREFLIGHT_USE_NEW_OUTPUT_DIRECTORY')
    trace_id = 'preflight-'+uuid4().hex
    records = []
    for role in roles:
        record = dict(artifact_type='blocked-preflight-record; not an execution receipt',
            specialist=role, role_id=role, responsibility=RESPONSIBILITIES[role],
            investigation_id=case.investigation_id, trace_id=trace_id,
            checked_at=datetime.now(timezone.utc).isoformat(), requested_model=MODEL,
            actual_returned_model=None, actual_route=None, provider_request_id=None,
            requested_at=None, completed_at=None, token_usage=None,
            requests=[], tool_selected=None, tool_arguments=None, tool_execution_status='not_attempted',
            tool_result_reference=None, tool_result_sha256=None, continuation_occurred=False,
            structured_finding=None, evidence_ids=[], live_verified=False,
            outcome='blocked_before_dispatch', blockers=list(BLOCKERS),
            existing_red_sea_native_loop=role in PROMPTS,
            existing_helene_read_tools=list(OperationalTools.ALLOWED.get(role, ())),
            scope_limit=('No saline clinical-outcome model exists; do not import the oncology model.'
                         if role=='clinical' else 'Read-only verification; no stock movement, approval or external communication.'))
        (output/(role+'.json')).write_text(json.dumps(record, indent=2)+'\n')
        records.append(dict(agent=role, live_nemotron=False, tool_call=False, continuation=False,
            record=role+'.json', outcome='blocked_before_dispatch'))
    summary = dict(classification='blocked; genuine inference not attempted', trace_id=trace_id,
        investigation_id=case.investigation_id, discovered_roles=roles,
        role_registry='phases/phase_05_product/integration.py:RESPONSIBILITIES',
        native_loop_registry='phases/phase_05_product/live_specialists.py:PROMPTS',
        specialists_discovered=len(roles), specialists_attempted=0, specialists_completed=0,
        actual_model_requests=0, input_tokens_sent=0, output_tokens_received=0,
        provider_reported_usage=None, inference_wall_clock_seconds=0,
        preflight_wall_clock_seconds=round(monotonic()-started,3),
        requested_limits=REQUESTED_LIMITS, requested_limits_verified_for_live_execution=False,
        dispatch_enabled=False, cost=dict(incurred_by_this_preflight=0,
            inference_route_no_charge_status='unverified; no inference dispatched'),
        blockers=list(BLOCKERS), agents=records,
        limitations=['Twelve responsibility labels are not twelve implemented native-model loops.',
            'The existing native entry point is Red Sea only and permits more than two requests per modeled role.',
            'These files document blocked non-attempts; none proves tool use or live inference.'])
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    service = OperatorService(args.store_root)
    cases = [c for c in service.helene_store.cases() if c.investigation_id.startswith('helene-2024-10-01-')
             and c.verification is None]
    if len(cases)!=1:
        raise ValueError('SELECT_SINGLE_UNMODIFIED_HELENE_CHECKPOINT_STORE')
    result=record_preflight(cases[0], args.output_dir)
    print(json.dumps(dict(discovered=result['discovered_roles'], actual_model_requests=0,
        attempted=0, completed=0, status='blocked', blockers=list(BLOCKERS)), indent=2))


if __name__ == '__main__':
    main()
