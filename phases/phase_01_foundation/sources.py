"""One bounded read per official source. No automatic retries or fixture fallback."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from contracts import DrugShortage, SourceEvidence, TradeFlow
from .http import ServiceError, request_json

FDA_URL = 'https://api.fda.gov/drug/shortages.json'
COMTRADE_URL = 'https://comtradeapi.un.org/public/v1/preview/C/A/HS'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def source_date(value):
    if not value:
        return None
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(value, fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def evidence(source, row, url, observed_at, *, fixture=False, source_id=None, raw_dir=None):
    sha = digest(row)
    raw_ref = None
    if raw_dir is not None:
        path = Path(raw_dir) / source / (sha + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n')
        raw_ref = str(path)
    return SourceEvidence(
        evidence_id=f'{source}:{sha}', kind='simulated' if fixture else 'observed',
        source=source, source_record_id=source_id, source_url=url, raw_ref=raw_ref,
        observed_at=observed_at, content_sha256=sha,
        published_on=source_date(row.get('initial_posting_date')),
        updated_on=source_date(row.get('update_date')),
        # Publication date is not proof of this revision's historical availability.
        version_available_at=None,
        note=('Synthetic offline fixture; no live retrieval.' if fixture else
              'Observed at retrieval; historical version availability unknown.'))


def bounded(limit):
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('Sample limit must be 1..5')


def normalize_fda(rows, url, observed_at, *, fixture=False, raw_dir=None):
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ServiceError('FDA_INVALID_RECORD')
        ev = evidence('fda', row, url, observed_at, fixture=fixture,
                      source_id=row.get('package_ndc'), raw_dir=raw_dir)
        result.append(DrugShortage(
            record_id=ev.evidence_id, generic_name=row.get('generic_name') or None,
            package_ndc=row.get('package_ndc') or None, company=row.get('company_name') or None,
            status=row.get('status') or None, availability=row.get('availability') or None,
            presentation=row.get('presentation') or None, evidence=ev))
    return result


def normalize_comtrade(rows, url, observed_at, *, fixture=False, raw_dir=None):
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ServiceError('COMTRADE_INVALID_RECORD')
        keys = ('typeCode', 'freqCode', 'classificationCode', 'period', 'reporterCode',
                'partnerCode', 'partner2Code', 'cmdCode', 'flowCode', 'customsCode', 'motCode')
        # Comtrade supplies a composite key, not an individual shipment ID.
        source_id = '|'.join(str(row.get(k, '')) for k in keys)
        ev = evidence('comtrade', row, url, observed_at, fixture=fixture, source_id=source_id, raw_dir=raw_dir)
        result.append(TradeFlow(
            record_id=ev.evidence_id, hs_code=row['cmdCode'],
            classification=row['classificationCode'], period=str(row['period']),
            reporter_code=row['reporterCode'], partner_code=row['partnerCode'],
            flow=row['flowCode'], value_usd=row.get('primaryValue'),
            is_aggregate=row.get('isAggregate'), is_reported=row.get('isReported'), evidence=ev))
    return result


def fetch_fda(client, limit=3, *, raw_dir=None):
    bounded(limit)
    body, url = request_json(client, 'GET', FDA_URL, params={'limit': limit})
    rows = body.get('results') if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) > limit:
        raise ServiceError('FDA_INVALID_RESPONSE')
    return normalize_fda(rows, url, datetime.now(timezone.utc), raw_dir=raw_dir)


def fetch_comtrade(client, limit=3, *, raw_dir=None):
    bounded(limit)
    body, url = request_json(client, 'GET', COMTRADE_URL, params={
        'period': '2023', 'reporterCode': 842, 'cmdCode': '30', 'flowCode': 'M',
        'partnerCode': 0, 'partner2Code': 0, 'customsCode': 'C00',
        'motCode': 0, 'maxRecords': limit})
    if not isinstance(body, dict) or body.get('error') or body.get('errorObject'):
        raise ServiceError('COMTRADE_API_ERROR', blocked=True)
    rows = body.get('data')
    if not isinstance(rows, list) or len(rows) > limit:
        raise ServiceError('COMTRADE_INVALID_RESPONSE')
    records = normalize_comtrade(rows, url, datetime.now(timezone.utc), raw_dir=raw_dir)
    if any(r.hs_code != '30' or r.period != '2023' or r.reporter_code != 842
           or r.partner_code != 0 or r.flow != 'M' for r in records):
        raise ServiceError('COMTRADE_FILTER_MISMATCH')
    return records
