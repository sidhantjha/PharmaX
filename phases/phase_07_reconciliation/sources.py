"""Explicit public acquisition; replay reads only hash-checked retained originals."""
import argparse
from datetime import datetime, date, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from bs4 import BeautifulSoup
import httpx
from pypdf import PdfReader
from contracts.models import SourceEvidence
from contracts.operational_claims import OperationalClaim
from contracts.replay import document_time

ROOT = Path(__file__).parent / 'fixtures/public'
SOURCES = [
    ('sc-dph', 'South Carolina DPH', '2024-10-04',
     'https://www.dph.sc.gov/sites/scdph/files/2024-10/10589-DHD-10-04-2024-IVS.pdf',
     ['October 4, 2024', 'Baxter', 'conserv']),
    ('va-doh', 'Virginia Department of Health', '2024-10-08',
     'https://www.vdh.virginia.gov/clinicians/clinician-letters/update-on-disruption-in-production-of-intravenous-iv-fluid-due-to-impacts-from-hurricane-helene/',
     ['October 8, 2024', 'Baxter has announced', 'conservation']),
    ('cdc-han-00518', 'CDC', '2024-10-12', 'https://www.cdc.gov/han/2024/han00518.html',
     ['October 12, 2024', 'North Cove', 'conserve']),
]


def extract(raw, pdf):
    if pdf:
        return '\n'.join(p.extract_text() for p in PdfReader(BytesIO(raw)).pages)
    return BeautifulSoup(raw, 'html.parser').get_text(' ', strip=True)


def acquire(root=ROOT):
    root = Path(root)
    if (root/'manifest.json').exists():
        raise ValueError('PUBLIC_MANIFEST_EXISTS')
    root.mkdir(parents=True, exist_ok=True)
    records = []
    # Exactly three allowlisted requests; reserve per-source byte budgets before dispatch.
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        for source_id, org, day, url, markers in SOURCES:
            raw = bytearray()
            with client.stream('GET', url) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    if len(raw)+len(chunk) > 4_194_304:
                        raise ValueError('PUBLIC_SOURCE_BYTE_BUDGET')
                    raw.extend(chunk)
            raw = bytes(raw)
            text = extract(raw, url.endswith('.pdf'))
            if not all(m.lower() in text.lower() for m in markers):
                raise ValueError('PUBLIC_SOURCE_CONTENT_MISMATCH')
            hashed = sha256(raw).hexdigest()
            file = hashed+('.pdf' if url.endswith('.pdf') else '.html')
            (root/file).write_bytes(raw)
            records.append(dict(source_id=source_id, organization=org, issue_date=day, url=url,
                file=file, sha256=hashed, retrieved_at=datetime.now(timezone.utc).isoformat(),
                custody='Exact historical version unproven; reconstructed issue-day availability only.',
                revision_note='VDH page says Last Updated January 21, 2026; no claim of 2024 byte availability.'
                              if source_id == 'va-doh' else 'Current retrieved version; historical custody unproven.'))
    (root/'manifest.json').write_text(json.dumps(records, indent=2)+'\n')
    return records


def load_public(cutoff, *, strict=False, root=ROOT):
    claims = []
    records = json.loads((Path(root)/'manifest.json').read_text())
    if len(records) != len(SOURCES):
        raise ValueError('PUBLIC_MANIFEST_COUNT')
    for record, expected in zip(records, SOURCES):
        source_id, org, day, url, markers = expected
        if (record['source_id'], record['organization'], record['issue_date'], record['url']) != expected[:4]:
            raise ValueError('PUBLIC_MANIFEST_IDENTITY')
        available = document_time(date.fromisoformat(day))
        if available > cutoff:
            continue
        path = Path(root)/record['file']
        if path.resolve().parent != Path(root).resolve():
            raise ValueError('PUBLIC_SOURCE_PATH')
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('PUBLIC_SOURCE_HASH_MISMATCH')
        text = extract(raw, path.suffix == '.pdf')
        if not all(m.lower() in text.lower() for m in markers):
            raise ValueError('PUBLIC_SOURCE_CONTENT_MISMATCH')
        if strict:
            raise ValueError('PUBLIC_HISTORICAL_CUSTODY_UNPROVEN')
        ev = SourceEvidence(evidence_id=source_id+'-'+record['sha256'][:16], kind='observed', source=org,
            source_record_id=source_id, source_url=url, observed_at=record['retrieved_at'],
            published_on=day, updated_on='2026-01-21' if source_id == 'va-doh' else None,
            content_sha256=record['sha256'], raw_ref=str(path),
            note=record['custody']+' '+record['revision_note'])
        for meaning in ('production_status', 'agency_guidance'):
            cid = source_id+'-'+meaning
            claims.append(OperationalClaim(claim_id=cid, source_id=source_id, organization=org,
                document_id=record['file'], record_id=cid,
                originating_claim_id='baxter-north-cove-disruption' if meaning == 'production_status' else cid,
                observation='repeated' if meaning == 'production_status' else 'independent',
                meaning=meaning, classification='real', unit='status', status='disrupted' if
                meaning == 'production_status' else 'conservation-guidance-issued', scope='policy',
                issued_at=available, effective_at=available, available_at=available, verification='verified',
                evidence=ev, reason='Agency recognized disruption and issued conservation guidance. '
                    'Manufacturing statement traced to Baxter; no independent hospital shipment verification. '
                    'Issue-day availability assumed in reconstructed simulation; exact 2024 text unproven.'))
    return claims


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--acquire', action='store_true')
    args = parser.parse_args()
    print(json.dumps(acquire() if args.acquire else [c.model_dump(mode='json') for c in
                     load_public(datetime.now(timezone.utc))], indent=2))
