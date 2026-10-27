"""Read-only CSV readiness, retaining per-file bytes, clocks and excluded records."""
import csv
import json
from datetime import date
from hashlib import sha256
from pathlib import Path
from phases.phase_06_helene.operational import import_operations
from .identity import catalog_identity, resolve, validate_catalog

REQUIRED = {
    'catalog': 'facility_id local_sku manufacturer_code ndc volume_ml strength_mg_ml total_mg base_unit case_bags account_id supplier_id',
    'inventory': 'record_id facility_id local_sku snapshot_at lot_id on_hand reserved unit expires_on quality',
    'consumption': 'record_id facility_id local_sku day quantity unit',
    'purchase_orders': 'record_id facility_id local_sku account_id supplier_id line_id quantity unit ordered_on expected_on',
    'acknowledgments': 'record_id facility_id local_sku account_id po_id line_id revision status quantity unit expected_on',
    'shipments': 'record_id facility_id local_sku account_id po_id line_id revision quantity unit dispatched_on arrival_on status',
    'facilities': 'facility_id name state network_id',
    'suppliers': 'facility_id supplier_id account_id approved transfers_allowed',
    'purchasing': 'record_id facility_id local_sku period_start period_end quantity returned unit',
}
OWNERS = {'catalog': 'Pharmacy item-master steward', 'inventory': 'Pharmacy inventory control',
          'consumption': 'Pharmacy dispensing analytics', 'facilities': 'Health-system administration',
          'suppliers': 'Supply-chain contracting'}


def inspect(folder, cutoff):
    folder = Path(folder)
    files, feeds, errors = {}, [], []
    try:
        clocks = json.loads((folder / 'clocks.json').read_text())
        if clocks.get('schema_version', '6a.1') not in {'6a.1', '8.1'}:
            raise ValueError('INVALID_SCHEMA_VERSION')
    except (OSError, ValueError) as exc:
        clocks = {}
        errors.append(str(exc))
    for feed, columns in REQUIRED.items():
        path = folder / (feed + '.csv')
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('CSV_BYTE_BUDGET:' + feed)
            raw = path.read_bytes()
            reader = csv.DictReader(raw.decode('utf-8-sig').splitlines())
            actual = reader.fieldnames or []
            rows = list(reader)
            if len(rows) > 10000:
                raise ValueError('CSV_RECORD_BUDGET:' + feed)
            missing = sorted(set(columns.split()) - set(actual))
            if missing or not rows or any(any(r.get(k) in (None, '') for k in columns.split()
                    if k not in {'expected_on'}) for r in rows):
                errors.append('MISSING_FIELDS_OR_RECORDS:' + feed)
            files[feed] = rows
            feeds.append(dict(source=feed, required_fields=columns.split(), actual_fields=actual,
                missing_fields=missing, records=len(rows), sha256=sha256(raw).hexdigest(),
                source_path=str(path), last_update=clocks.get('exported_at'),
                owner=OWNERS.get(feed, 'Purchasing / materials management'),
                classification='synthetic ERP export; actual local ingestion',
                live_access='Unverified; partner read-only export/view and owner approval required.'))
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            errors.append('UNREADABLE_FEED:' + feed + ':' + type(exc).__name__)
    identities = []
    for row in files.get('catalog', []):
        try:
            identities.append(resolve(catalog_identity(row)))
        except (ValueError, KeyError) as exc:
            errors.append('INVALID_CATALOG:' + str(exc))
    # Lot identity cannot be collapsed across snapshots, even when record IDs differ.
    seen, distributor = set(), {}
    for row in files.get('inventory', []):
        # This adapter admits one canonical product: alternate local SKUs cannot duplicate its lot.
        key = tuple(row.get(k) for k in ('facility_id', 'lot_id'))
        if key in seen:
            errors.append('DUPLICATE_LOT_SNAPSHOT')
        seen.add(key)
    for row in files.get('catalog', []):
        if row.get('distributor_item'):
            key = (row.get('supplier_id'), row['distributor_item'])
            value = (row.get('manufacturer_code'), row.get('ndc'))
            if key in distributor and distributor[key] != value:
                errors.append('CONFLICTING_SUPPLIER_ITEM')
            distributor[key] = value
    excluded = []
    for row in files.get('inventory', []):
        try:
            if row['quality'] != 'released' or date.fromisoformat(row['expires_on']) < cutoff.date():
                excluded.append(dict(record_id=row['record_id'], lot_id=row['lot_id'],
                    reason=row['quality'] if row['quality'] != 'released' else 'expired'))
        except (KeyError, ValueError):
            errors.append('MISSING_OR_INVALID_LOT_EXPIRY')
    operations, receipts = None, []
    if not errors:
        try:
            operations, receipts, _ = import_operations(folder, cutoff,
                identity_resolver=validate_catalog, exclude_unusable=True)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
    facilities = sorted({r.get('facility_id', 'unknown') for r in files.get('facilities', [])})
    unresolved = [i['original']['local_sku'] for i in identities if i['review_required']]
    return operations, receipts, dict(schema_version='8.1', feeds=feeds, identities=identities,
        excluded_lots=excluded, errors=errors, classification='rejected' if errors else 'usable_with_assumptions',
        supports_forecast=operations is not None,
        hospitals=[dict(facility_id=h, mapping_coverage=sum(not i['review_required'] for i in identities
            if i['original']['source_organization'] == h), unresolved_identifiers=unresolved,
            missing_records=[e for e in errors if 'MISSING' in e], supports_forecast=operations is not None)
            for h in facilities], limitations=[
            'Hospital, distributor and lot records are fictional; local import is real.',
            'Public manufacturer evidence remains separate from ERP assertions.',
            'Live hospital access and live Zetaris federation are unverified.',
            'Minimum partner access: read-only item master, lot inventory, dispensing history, open orders.',
            'Optional: receiving, supplier acknowledgment and shipment feeds; pharmacy and IT approval required.',
            'CSV contract is the default; existing FHIR R5 snapshot subset remains available; no generic EDI support.'])


def import_csv(folder, cutoff):
    ops, receipts, report = inspect(folder, cutoff)
    if ops is None:
        raise ValueError('OPERATIONAL_INPUT_REJECTED:' + ';'.join(report['errors']))
    return ops, receipts, report


def replay_importer(folder, cutoff):
    ops, receipts, _ = import_csv(folder, cutoff)
    receipts = [r.model_copy(update={'mapping_version':'nc-csv-v8.1'}) for r in receipts]
    files = {feed: list(csv.DictReader((Path(folder) / (feed+'.csv')).read_text().splitlines()))
             for feed in REQUIRED}
    return ops, receipts, files
