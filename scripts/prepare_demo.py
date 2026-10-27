"""Explicit public-source bootstrap, offline editable examples, and saved-run restoration.

No private configuration is read. No model or authenticated data client is constructed.
The optional download step is separate from offline sample execution.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sources(download):
    for folder in ('phases/phase_06_helene/fixtures/manufacturer',
                   'phases/phase_07_reconciliation/fixtures/public'):
        base = ROOT / folder
        for record in json.loads((base / 'manifest.json').read_text()):
            # The default case is October 1; later agency guidance is not admitted.
            if folder.endswith('/public') and record['issue_date'] > '2024-10-01':
                continue
            path = base / record['file']
            if not path.is_file():
                if not download:
                    raise SystemExit('Public originals missing. Run prepare_demo.py --fetch-sources first.')
                # Exactly seven manifest URLs, at most 4 MiB each, 30 seconds per request.
                request = urllib.request.Request(record['url'], headers={'User-Agent': 'PharmaX-reproducibility/1.0'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read(4_194_305)
                if len(raw) > 4_194_304 or sha256(raw).hexdigest() != record['sha256']:
                    raise SystemExit('Pinned public source changed or exceeded budget: ' + record['url'])
                path.write_bytes(raw)
            if sha256(path.read_bytes()).hexdigest() != record['sha256']:
                raise SystemExit('Retained source hash mismatch: ' + record['file'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-sources', action='store_true', help='Explicit network bootstrap for pinned public documents')
    args = parser.parse_args()
    sources(args.fetch_sources)
    # From this point the existing fixture path performs no source network calls.
    from contracts.replay import HeleneCase
    from phases.phase_05_product.service import OperatorService
    from phases.phase_05_product.native_status import publish
    sample_output = ROOT / '.runtime/examples'
    if not sample_output.exists():
        subprocess.run([sys.executable, '-m', 'phases.phase_08_operations.judge_samples',
                        '--output-dir', str(sample_output), '--store-root', str(ROOT / '.runtime/example-store')],
                       cwd=ROOT, check=True)
    summary = json.loads((sample_output / 'summary.json').read_text())
    assert [r['unmet']['sim-nc-central'] for r in summary] == [172, 72, 0]
    service = OperatorService(ROOT / '.runtime/demo')
    case = HeleneCase.model_validate_json((ROOT / 'docs/evidence/recorded-case.json').read_text())
    try:
        service.helene_store.get(case.investigation_id)
    except KeyError:
        service.helene_store.save(case)
    proof = json.loads((ROOT / 'docs/evidence/pharmax-sponsors/zetaris-final-verified/proof.json').read_text())
    status = publish(service, ROOT / 'docs/evidence/pharmax-sponsors/full-agent-run/final', proof)
    assert (sum(f.total_unmet for f in case.forecasts), sum(f.total_unmet for f in case.mitigated)) == (342, 6)
    assert (status.roles_executed, status.findings_validated, status.model_requests) == (12, 11, 27)
    print(json.dumps({'status': 'passed', 'sample_central_unmet': [r['unmet']['sim-nc-central'] for r in summary],
                      'recorded_case': case.investigation_id, 'projected_network_unmet': 342,
                      'after_evaluated_mitigation': 6, 'new_model_calls': 0,
                      'recorded_model_requests': status.model_requests}, indent=2))


if __name__ == '__main__':
    main()
