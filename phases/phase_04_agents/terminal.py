"""Visible real tool activity and frontend-readable structured investigation trace."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from phases.phase_01_foundation.config import load_env
from phases.phase_01_foundation.http import make_client
from phases.phase_02_data.pipeline import load_bundle
from .coordinator import investigate
from .models import InvestigationRequest
from .reasoning import NemotronReasoner
from .trace import Trace

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=['demo', 'original', 'clean-onset'], default='demo')
    parser.add_argument('--mitigation', action='store_true', help='Run the adaptive Phase 4B coordinator.')
    parser.add_argument('--horizon-days', type=int, default=14)
    parser.add_argument('--candidate-budget', type=int, choices=range(1, 7), default=None)
    parser.add_argument('--tool-budget', type=int, choices=range(1, 31), default=20)
    parser.add_argument('--mode', choices=['cached', 'fixture', 'live-zetaris'], default='cached')
    parser.add_argument('--live-model', action='store_true', help='At most two Phase 4A or four Phase 4B Nemotron decision POSTs; no retries.')
    parser.add_argument('--allow-paid-model', action='store_true',
                        help='Explicitly authorize the two-call budget even if billed.')
    parser.add_argument('--env-file')
    parser.add_argument('--zetaris-env-file')
    parser.add_argument('--node', help='Node >=22.22 executable, or PHARMAX_NODE.')
    parser.add_argument('--session', default='demo')
    parser.add_argument('--as-of', default='2026-10-24T23:59:59+00:00')
    parser.add_argument('--historical', action='store_true')
    parser.add_argument('--objective')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    mitigation_mode = args.mitigation or args.scenario != 'demo'
    args.objective = args.objective or (
        'Investigate simulated supply alternatives, reject unsafe options and replan for human review.'
        if mitigation_mode else 'Investigate trade exposure, FDA disruption evidence and consequences for all three hospitals.')
    args.output_dir = args.output_dir or (ROOT / '.runtime/pharmax-p4b' / args.scenario
                                         if mitigation_mode else ROOT / '.runtime/pharmax-p4a')
    if mitigation_mode and args.mode != 'cached':
        parser.error('Mitigation uses the pinned cached dataset with explicitly simulated operations; select --mode cached')
    if mitigation_mode and args.live_model and args.candidate_budget and args.candidate_budget > 4:
        parser.error('Live mitigation budget is at most four decisions')
    if args.allow_paid_model and not args.live_model:
        parser.error('--allow-paid-model requires --live-model')
    if args.mode == 'fixture' and (args.live_model or args.env_file or args.zetaris_env_file):
        parser.error('Fixture mode disallows network/model/credential configuration')
    if args.mode != 'fixture':
        load_env(args.env_file)
        load_env(args.zetaris_env_file, prefix='ZETARIS_')
    dataset = ROOT / ('phases/phase_02_data/fixtures/bundle.json' if args.mode == 'fixture'
                      else 'docs/evidence/pharmax-phase2/dataset.json')
    bundle = load_bundle(dataset)
    request = InvestigationRequest(session_id=args.session, objective=args.objective,
        evidence_as_of=datetime.fromisoformat(args.as_of), mode=args.mode, historical=args.historical)
    trace = Trace(emit=lambda line: print(line, flush=True))
    client = make_client() if args.live_model else None
    try:
        if mitigation_mode:
            from contracts import MitigationRequest
            from .coordinator import investigate_mitigation
            from .mitigation_reasoning import NemotronMitigationReasoner
            from .mitigation_scenario import load_catalog
            mitigation_request = MitigationRequest(session_id=args.session, objective=args.objective,
                scenario='original' if args.scenario == 'demo' else args.scenario,
                evidence_as_of=request.evidence_as_of, horizon_days=args.horizon_days,
                reasoning='nightshift' if args.live_model else 'fixture')
            reasoner = NemotronMitigationReasoner(client, allow_paid=args.allow_paid_model) if client else None
            result = investigate_mitigation(bundle, load_catalog(), mitigation_request, args.output_dir / 'memory',
                reasoner=reasoner, trace=trace, node=args.node,
                max_candidates=args.candidate_budget or (4 if args.live_model else 6), max_calls=args.tool_budget)
        else:
            reasoner = NemotronReasoner(client, allow_paid=args.allow_paid_model) if client else None
            result = investigate(bundle, request, args.output_dir / 'memory', reasoner=reasoner,
                                 trace=trace, node=args.node)
    finally:
        if client:
            client.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = result.model_dump(mode='json')
    output['completed_at'] = datetime.now(timezone.utc).isoformat()
    (args.output_dir / 'investigation.json').write_text(json.dumps(output, indent=2) + '\n')
    print(f'RESULT {result.status}; trace={args.output_dir / "investigation.json"}; error={result.error_code}')
    return 0 if result.status == 'complete' else 2
