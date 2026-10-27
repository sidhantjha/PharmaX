"""Single offline demo/serve entry point; public acquisition is a separate command."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from phases.phase_04_agents.live_intelligence.service import serve
from phases.phase_05_product.service import OperatorService
from .fixtures import DAYS
from .pipeline import replay_checkpoint


def seed(service):
    cases = service.helene_store.cases()
    if cases:
        for case in cases:
            for receipt in case.admission.imports:
                clocks = json.loads((Path(receipt.source_path).parent / "clocks.json").read_text())
                if (
                    receipt.exported_at != datetime.fromisoformat(clocks["exported_at"])
                    or receipt.modeled_available_at != datetime.fromisoformat(clocks["modeled_available_at"])
                ):
                    raise ValueError("SAVED_IMPORT_CLOCK_CHANGED_REIMPORT_REQUIRED")
            for ev in case.evidence:
                if not ev.raw_ref:
                    continue
                if sha256(Path(ev.raw_ref).read_bytes()).hexdigest() != ev.content_sha256:
                    raise ValueError("SAVED_INPUT_CHANGED_REIMPORT_REQUIRED")
        return cases
    parent = None
    for day in DAYS:
        case = replay_checkpoint(
            day, parent=parent, memory_root=service.root / "helene-memory", node=service.node
        )
        service.helene_store.save(case)
        parent = case
        cases.append(case)
    return cases


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, default=Path(".runtime/helene"))
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--with-red-sea", action="store_true")
    args = p.parse_args(argv)
    service = OperatorService(args.output_dir)
    cases = seed(service)
    if args.with_red_sea and service.store.summaries(1)[0] == 0:
        from contracts.operator import CaseRequest
        from contracts.reconciliation import ReconciliationRequest

        parent = service.create(CaseRequest(scenario="red-sea"))
        service.reconcile(parent.investigation_id, ReconciliationRequest())
    receipt = [
        dict(
            id=c.investigation_id,
            checkpoint=str(c.admission.sources[-1].issue_date),
            before={f.hospital_id: f.total_unmet for f in c.forecasts},
            after={f.hospital_id: f.total_unmet for f in c.mitigated},
            selected=c.selected,
            memory_writes=c.memory.get("writes", []),
        )
        for c in cases
    ]
    (args.output_dir / "replay.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2), flush=True)
    if args.serve:
        serve(service.registry, args.port, operator=service)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
