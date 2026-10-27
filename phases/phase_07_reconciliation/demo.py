"""Compute/persist real Phase 7 cases in the existing operator store and serve it."""
import argparse
import json
from pathlib import Path
from phases.phase_04_agents.live_intelligence.service import serve
from phases.phase_05_product.service import OperatorService
from phases.phase_06_helene.demo import seed
from .pipeline import reconcile_scenario


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=Path('.runtime/phase7'))
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8877)
    parser.add_argument('--with-red-sea', action='store_true')
    args = parser.parse_args()
    service = OperatorService(args.output_dir)
    seed(service)
    existing = service.helene_store.cases()
    original = next(c for c in existing if not c.parent_id and c.investigation_id.startswith('helene-2024-09-29'))
    results = []
    for scenario in ('A', 'B', 'C'):
        case = next((c for c in existing if c.verification and c.verification.scenario == scenario), None)
        if case is None:
            case = reconcile_scenario(scenario, parent=original, memory_root=service.root/'helene-memory', node=service.node)
            service.helene_store.save(case)
        results.append(dict(id=case.investigation_id, scenario=scenario,
            receipts=case.verification.physical_receipts,
            findings=[f.model_dump(mode='json') for f in case.verification.findings],
            unmet={f.hospital_id:f.total_unmet for f in case.forecasts},
            conditional=case.verification.conditional_forecasts, requires_human_review=case.verification.requires_human_review,
            selected=case.selected, memory=case.memory))
    if args.with_red_sea and service.store.summaries(1)[0] == 0:
        from contracts.operator import CaseRequest
        from contracts.reconciliation import ReconciliationRequest
        parent = service.create(CaseRequest(scenario='red-sea'))
        service.reconcile(parent.investigation_id, ReconciliationRequest())
    (args.output_dir/'reconciliation.json').write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps([{k:v for k,v in r.items() if k in {'id','scenario','receipts','unmet','selected','requires_human_review'}}
                      for r in results], indent=2), flush=True)
    if args.serve:
        serve(service.registry, args.port, operator=service)


if __name__ == '__main__':
    main()
