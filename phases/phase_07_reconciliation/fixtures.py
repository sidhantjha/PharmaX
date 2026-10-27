"""Reproducible independent raw CSV procurement inputs; no forecast outputs."""
import csv
import json
from pathlib import Path
import shutil
from phases.phase_06_helene.pipeline import OPERATIONS
from phases.phase_06_helene.fixtures import write_csv

ROOT = Path(__file__).parent/'fixtures/scenarios'
SCENARIOS = ('A', 'B', 'C')
DAY = '2024-10-01'


def generate(root=ROOT):
    for scenario in SCENARIOS:
        folder = Path(root)/scenario/DAY
        folder.mkdir(parents=True, exist_ok=True)
        for source in (OPERATIONS/DAY).iterdir():
            shutil.copyfile(source, folder/source.name)
        for feed in ('purchase_orders', 'acknowledgments', 'shipments', 'consumption'):
            path = folder/(feed+'.csv')
            rows = list(csv.DictReader(path.read_text().splitlines()))
            if feed in ('acknowledgments', 'shipments') and scenario != 'B':
                rows = [r for r in rows if r['facility_id'] != 'sim-nc-central']
            for row in rows:
                if row['facility_id'] != 'sim-nc-central':
                    continue
                if feed == 'purchase_orders':
                    row.update(quantity='1000', ordered_on='2024-09-28', expected_on='2024-09-30')
                elif feed == 'acknowledgments':
                    row.update(quantity='800', expected_on='2024-09-30')
                elif feed == 'shipments':
                    row.update(quantity='800', dispatched_on='2024-09-29', arrival_on='2024-09-30')
                elif scenario == 'B':
                    # Explicit synthetic high-demand ward mix; 60 bags/day, not an observed hospital.
                    row['quantity'] = '60'
            write_csv(path, rows)
        receiving = [dict(record_id='receipt-central', facility_id='sim-nc-central', local_sku='IV-NS1L',
            account_id='sim-account-central', po_id='po-central', line_id='line-1', shipment_id='asn-central',
            ndc='00338-0049-04', quantity='500', unit='bag', case_bags='', status='closed', scope='event_total',
            effective_at='2024-10-01T23:30:00-04:00', available_at='2024-10-02T00:00:00-04:00',
            expires_on='2024-12-31', quality='released')]
        # Header-only receiving feed explicitly represents absence.
        with (folder/'receiving.csv').open('w', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=list(receiving[0]))
            writer.writeheader()
            if scenario == 'B':
                writer.writerows(receiving)
        (folder/'scenario.json').write_text(json.dumps(dict(classification='simulated', scenario=scenario,
            assumptions=['Fictional three-hospital system; no real procurement or physical delivery.',
                         'Case B central demand is explicitly simulated at 60 bags/day.',
                         'Receipt is a closed accepted cumulative total after delivery window.',
                         'Receipts after 23:00 stock snapshot add released lots; expiry is independently supplied.']),
            indent=2)+'\n')
    return str(root)


if __name__ == '__main__':
    print(generate())
