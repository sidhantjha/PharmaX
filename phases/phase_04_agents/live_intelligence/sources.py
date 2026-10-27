"""Fixed, bounded public sources. No article-body scraping or inference from indexed text."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit, urlunsplit
import httpx
from contracts import DisruptionSignal, GeoBox, SourceEvidence
from phases.phase_01_foundation.http import ServiceError
from phases.phase_01_foundation.sources import digest

MARAD_INDEX = 'https://www.maritime.dot.gov/msci-advisories'
MARAD_PILOT = 'https://www.maritime.dot.gov/msci/2026-013-red-sea-bab-el-mandeb-strait-gulf-aden-arabian-sea-and-somali-basin-houthi-attacks'
GDELT_URL = 'https://api.gdeltproject.org/api/v2/doc/doc'
SEARCH = '("Red Sea" OR "Suez disruption" OR "port closure" OR "port congestion" OR "pharmaceutical manufacturing" OR "trade restriction" OR "shipping disruption")'
BOXES = {'red-sea': (32, 12, 44, 30), 'bab-el-mandeb': (42, 12, 44, 14),
         'gulf-of-aden': (43, 10, 52, 16), 'arabian-sea': (50, 5, 74, 26),
         'suez': (32, 29, 33, 32)}
ALIASES = {'red-sea': ('red sea',), 'bab-el-mandeb': ('bab el mandeb', 'bab-el-mandeb'),
           'gulf-of-aden': ('gulf of aden',), 'arabian-sea': ('arabian sea',), 'suez': ('suez',)}


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.parts, self.rows, self.h1 = [], [], []
        self.row, self.cell = None, None
        self.in_h1 = False
        self.heading = []
        self.meta = {}
        self.skip = 0
        self.feed(body.decode('utf-8'))

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'meta':
            data = dict(attrs)
            self.meta[data.get('property') or data.get('name') or data.get('itemprop', '')] = data.get('content')
        if tag == 'h1':
            self.in_h1 = True
            self.heading = []
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip-1)
        if tag == 'h1':
            self.h1.append(' '.join(self.heading))
            self.in_h1 = False
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None:
                self.row.append(' '.join(self.cell))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, text):
        if self.skip:
            return
        clean = ' '.join(text.split())
        if clean:
            self.parts.append(clean)
            if self.in_h1:
                self.heading.append(clean)
            if self.cell is not None:
                self.cell.append(clean)


def corridors(text):
    text = text.lower()
    return sorted(k for k, phrases in ALIASES.items() if any(p in text for p in phrases))


def boxes(names):
    return [GeoBox(west=BOXES[k][0], south=BOXES[k][1], east=BOXES[k][2], north=BOXES[k][3]) for k in names]


def evidence(snapshot, provider, record=None):
    key, receipt, _ = snapshot
    return SourceEvidence(evidence_id=provider + ':' + digest({'body': receipt['body_sha256'], 'record': record}),
        kind='observed', source=provider, source_record_id=record, source_url=receipt['url'],
        observed_at=receipt['observed_at'], content_sha256=receipt['body_sha256'], raw_ref='snapshot:' + key,
        note='Retrieval establishes availability of this revision only; no backdating to publication.')


def sign(values):
    candidate = DisruptionSignal.model_validate({**values, 'revision_id': '0' * 64})
    semantic = candidate.model_dump(mode='json', exclude={'revision_id', 'retrieved_at', 'evidence', 'evidence_ids', 'indexed_at'})
    return candidate.model_copy(update={'revision_id': digest(semantic)})


def parse_marad(index_snapshot, detail_snapshot):
    page, index = Page(detail_snapshot[2]), Page(index_snapshot[2])
    if index_snapshot[1]['url'] != MARAD_INDEX or detail_snapshot[1]['url'] != MARAD_PILOT:
        raise ServiceError('MARAD_ORIGIN_INVALID')
    title = next((h for h in page.h1 if re.match(r'\d{4}-\d{3}[-\s]', h)), '')
    match = re.match(r'(\d{4}-\d{3})[-\s]', title)
    if not match:
        raise ServiceError('MARAD_TITLE_INVALID')
    identifier = match[1]
    row = next((r for r in index.rows if len(r) >= 4 and r[0].startswith(identifier)), None)
    if row is None or row[1].lower() not in ('active', 'cancelled', 'canceled'):
        raise ServiceError('MARAD_METADATA_MISSING')
    try:
        start, end = (datetime.strptime(s.strip(), '%m/%d/%Y').date() for s in row[2:4])
    except ValueError:
        raise ServiceError('MARAD_DATES_INVALID') from None
    body = ' '.join(page.parts)
    supersedes = sorted(set(re.findall(r'(?:supersedes and cancels|cancels) U\.S\. Maritime Advisory (\d{4}-\d{3})', body)))
    names = corridors(title)
    evs = [evidence(index_snapshot, 'marad', 'listing-' + identifier), evidence(detail_snapshot, 'marad', identifier)]
    fetched = max(e.observed_at for e in evs)
    claims = ['MARAD published a maritime security advisory covering the locations stated in its title.',
              f'The official listing states status {row[1]}, effective {start}, expiry {end}.']
    if 'threat' in body.lower() and 'commercial' in body.lower():
        claims.append('The advisory describes security threats to commercial vessels; this is an authority statement, not an independent incident verification.')
    if supersedes:
        claims.append('The notice explicitly supersedes/cancels ' + ', '.join(supersedes) + '.')
    return sign(dict(event_id='marad:' + identifier, deduplication_identity='marad:' + identifier,
        category='maritime_security', title=title, provider='marad', source_type='official', publisher='U.S. MARAD',
        original_urls=[MARAD_PILOT, MARAD_INDEX], location=[k.replace('-', ' ') for k in names], corridors=names,
        geography=boxes(names), retrieved_at=fetched, effective_on=start, expires_on=end,
        # Effective date is NOT asserted to be publication time.
        source_published_on=None, status='active' if row[1].lower() == 'active' else 'cancelled',
        supersedes=['marad:' + s for s in supersedes], confirmation='official_notice', supported_claims=claims,
        claim_limitations=['Publication timestamp is not supplied by the parsed notice/listing; effective date is separate.',
            'Coarse geography is a curated interpretation of named waters, not an observed vessel track.',
            'An advisory does not prove delay, route use, supplier dependency or carboplatin impact.'],
        evidence_ids=[e.evidence_id for e in evs], evidence=[e.model_dump(mode='json') for e in evs]))


def canonical_url(value):
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or len(value) > 2000:
        raise ValueError('Invalid article URL')
    return urlunsplit((p.scheme, p.netloc.lower(), p.path or '/', p.query, ''))


def parse_gdelt(snapshot):
    try:
        body = json.loads(snapshot[2])
        articles = body['articles']
        if not isinstance(articles, list) or len(articles) > 12:
            raise ValueError
        results = {}
        for item in articles:
            url = canonical_url(item['url'])
            title = item['title']
            if not isinstance(title, str) or not 1 <= len(title) <= 1000:
                raise ValueError
            seen = datetime.strptime(item['seendate'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc) if item.get('seendate') else None
            ev = evidence(snapshot, 'gdelt', url)
            if seen and seen > ev.observed_at:
                raise ValueError
            names = corridors(title)
            lower = title.lower()
            category = ('port_disruption' if 'port' in lower and any(t in lower for t in ('closure', 'congestion'))
                        else 'manufacturing' if 'pharma' in lower or 'manufactur' in lower
                        else 'trade_restriction' if 'restriction' in lower or 'tariff' in lower else 'shipping')
            values = dict(event_id='gdelt:' + digest(url), deduplication_identity=url, category=category,
                title=title, provider='gdelt', source_type='media', publisher=item.get('domain') or urlsplit(url).hostname,
                original_urls=[url], location=[k.replace('-', ' ') for k in names], corridors=names,
                # No geocoded assertion from a title; names are provisional discovery hints.
                geography=[], retrieved_at=ev.observed_at, indexed_at=seen, source_published_at=None,
                source_published_on=None, status='unknown', confirmation='unverified_media',
                supported_claims=['GDELT indexed this article URL and title; no article-body claim was verified.'],
                claim_limitations=['Indexing is not verification. Title terms are provisional location/category hints.',
                    'Article publication/event time is unavailable; seendate is retained separately as index time.',
                    'No article body fetched, summary inferred, or precise operational delay established.'],
                evidence_ids=[ev.evidence_id], evidence=[ev.model_dump(mode='json')])
            signal = sign(values)
            results[signal.revision_id] = signal
        return list(results.values())
    except (ValueError, KeyError, TypeError):
        raise ServiceError('GDELT_SCHEMA_INVALID') from None


class Fetcher:
    """Three GETs per manual combined refresh; one retry only on a later scheduled check."""
    def __init__(self, snapshots, client, *, max_calls=3):
        if type(max_calls) is not int or not 1 <= max_calls <= 3:
            raise ValueError('Public fetch budget must be 1..3')
        self.snapshots, self.client, self.max_calls = snapshots, client, max_calls
        self.calls, self.receipts = 0, []

    def get(self, url, *, params=None):
        if url not in (MARAD_INDEX, MARAD_PILOT, GDELT_URL):
            raise ServiceError('SOURCE_URL_DENIED')
        if self.client is None or self.calls >= self.max_calls:
            raise ServiceError('PUBLIC_FETCH_BUDGET')
        self.calls += 1
        limit = 512_000 if url == GDELT_URL else 1_500_000
        try:
            with self.client.stream('GET', url, params=params, timeout=20) as response:
                if response.status_code != 200:
                    retry = response.headers.get('retry-after', '')
                    diagnostics = {'retry_after_seconds': min(86400, int(retry))} if retry.isdigit() else {}
                    raise ServiceError(f'HTTP_{response.status_code}', diagnostics=diagnostics)
                content_type = response.headers.get('content-type', '')
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > limit:
                        raise ServiceError('SOURCE_RESPONSE_BUDGET')
                result = self.snapshots.save('curated-document', str(response.url), bytes(body), content_type)
                self.receipts.append(result[0])
                return result
        except httpx.TimeoutException:
            raise ServiceError('SOURCE_TIMEOUT') from None
        except httpx.RequestError:
            raise ServiceError('SOURCE_UNREACHABLE') from None

    def fetch(self, source):
        if source == 'marad':
            return [parse_marad(self.get(MARAD_INDEX), self.get(MARAD_PILOT))]
        if source == 'gdelt':
            return parse_gdelt(self.get(GDELT_URL, params={'query': SEARCH, 'mode': 'artlist',
                'format': 'json', 'maxrecords': 12, 'timespan': '1day', 'sort': 'datedesc'}))
        raise ServiceError('SOURCE_DENIED')


def frozen(snapshots, manifest):
    """Hash-verified receipt replay only; deliberately has no HTTP client argument."""
    result = []
    marad = manifest.get('marad')
    if marad:
        result.append(parse_marad(snapshots.read(marad[0]), snapshots.read(marad[1])))
    if manifest.get('gdelt'):
        result.extend(parse_gdelt(snapshots.read(manifest['gdelt'][0])))
    return result
