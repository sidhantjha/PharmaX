"""Three editable CSV inputs and real canonical outputs; no model inference."""
import argparse
import csv
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
from phases.phase_06_helene.pipeline import OPERATIONS
from .zetaris_replay import execute


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--store-root',type=Path,required=True)
    p.add_argument('--verify-zetaris-mutation',action='store_true')
    p.add_argument('--private-env',type=Path)
    args=p.parse_args()
    if args.output_dir.exists():
        raise ValueError('FRESH_SAMPLE_OUTPUT_REQUIRED')
    reports=[]
    for name,delta in [('baseline',0),('additional-100-bags',100),('additional-300-bags',300)]:
        sample=args.output_dir/name
        folder=sample/'inputs/2024-10-01'
        shutil.copytree(OPERATIONS/'2024-10-01',folder)
        path=folder/'inventory.csv'
        with path.open() as f:
            reader=csv.DictReader(f)
            fields=reader.fieldnames
            rows=list(reader)
        central=next(r for r in rows if r['facility_id']=='sim-nc-central' and r['unit']=='bag')
        central['on_hand']=str(int(central['on_hand'])+delta)
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        clocks=json.loads((folder/'clocks.json').read_text())
        clocks['exported_at']=datetime.now(timezone.utc).isoformat()
        (folder/'clocks.json').write_text(json.dumps(clocks,indent=2)+'\n')
        case,proof=execute(sample/'offline-proof',args.store_root,operations=sample/'inputs',mode='offline')
        (sample/'case.json').write_text(case.model_dump_json(indent=2)+'\n')
        reports.append(dict(sample=name,mode=proof['mode'],classification='Actual execution over newly exported explicitly simulated CSVs',
            input_path=str(folder),added_synthetic_bags=delta,case_id=case.investigation_id,
            unmet={f.hospital_id:f.total_unmet for f in case.forecasts},live_model_calls=0))
    central=[r['unmet']['sim-nc-central'] for r in reports]
    if not central[0]>central[1]>central[2]:
        raise ValueError('RAW_INPUT_MUTATION_DID_NOT_CHANGE_FORECAST')
    (args.output_dir/'summary.json').write_text(json.dumps(reports,indent=2)+'\n')
    if args.verify_zetaris_mutation:
        case,proof=execute(args.output_dir/'zetaris-mutated-source',args.store_root,
            private_env=args.private_env,operations=args.output_dir/'additional-100-bags/inputs',mode='live')
        if case.forecasts[0].total_unmet!=reports[1]['unmet']['sim-nc-central']:
            raise ValueError('REMOTE_MUTATION_FORECAST_MISMATCH')
        reports.append(dict(sample='actual-zetaris-source-mutation',mode=proof['mode'],
            case_id=case.investigation_id,returned_rows=proof['returned_rows'],
            unmet={f.hospital_id:f.total_unmet for f in case.forecasts},live_model_calls=0))
    (args.output_dir/'summary.json').write_text(json.dumps(reports,indent=2)+'\n')
    print(json.dumps(reports,indent=2))


if __name__=='__main__':
    main()
