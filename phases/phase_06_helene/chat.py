"""A deterministic coordinator over canonical Helene evidence and saved specialists.

No model client, provider, SQL from the user, file refresh or network dispatch.
Source bytes and saved findings are data, never executable instructions.
"""
import json
import re
from datetime import date
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

from contracts.helene_chat import (
    HeleneChatRequest, HeleneChatResponse, HeleneChatTurn, ChatCitation, ChatExecution,
)
from phases.phase_05_product.native_status import read_status
from .pipeline import forecast_operations

LIMITATIONS = [
    'Offline deterministic synthesis; no model executed for this conversation.',
    'Hospital operations and physical actions are simulated; human pharmacy review is required.',
    'Manufacturer publication dates do not establish historical availability of revised documents.',
]


def manufacturer_extract(revision):
    """Read only a hash-bound PDF in the fixed retained manufacturer directory."""
    from pypdf import PdfReader
    from .sources import ROOT
    hashed = revision.evidence.content_sha256
    if not re.fullmatch('[a-f0-9]{64}', hashed):
        raise ValueError('CHAT_DOCUMENT_HASH_INVALID')
    path = ROOT / (hashed+'.pdf')
    if not path.exists():
        return None
    if path.stat().st_size > 4_194_304:
        raise ValueError('CHAT_DOCUMENT_BYTE_BUDGET')
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != hashed:
        raise ValueError('CHAT_DOCUMENT_HASH_MISMATCH')
    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 12:
        raise ValueError('CHAT_DOCUMENT_PAGE_BUDGET')
    return ' '.join((reader.pages[0].extract_text() or '').split())[:10000]


def initialize(service):
    with service.store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS helene_conversations '
                   '(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL, topic TEXT NOT NULL)')


def source(service, case, key):
    """Exact allowlist of read-only case resources; no path/file interpolation."""
    if key == 'manufacturer':
        return dict(sources=[s.model_dump(mode='json') for s in case.admission.sources],
                    allocations=[a.model_dump(mode='json') for a in case.admission.allocations],
                    first_page_extracts=[dict(revision_id=s.revision_id, page=1, text=manufacturer_extract(s))
                                         for s in case.admission.sources])
    if key == 'forecast':
        return dict(kind='derived', forecasts=[f.model_dump(mode='json') for f in case.forecasts],
                    mitigated=[f.model_dump(mode='json') for f in case.mitigated])
    if key == 'mitigation':
        return dict(kind='derived', candidates=case.candidates, selected=case.selected,
                    actions=service.helene_store.actions(case), memory=case.memory)
    if key == 'reconciliation':
        if case.verification:
            verification = case.verification
        else:
            from phases.phase_07_reconciliation.pipeline import verify_case
            verification = verify_case(case)
        return dict(verification=verification.model_dump(mode='json'), supply=case.reconciliation)
    if key == 'agents':
        status = read_status(service, case.investigation_id)
        return dict(saved_execution=status.model_dump(mode='json'), deterministic_tools=case.decisions)
    if key == 'evidence':
        return dict(records=[e.model_dump(mode='json') for e in case.evidence])
    raise KeyError('CHAT_SOURCE_NOT_FOUND')


def scenario(case, bags, hospital, arrival):
    if not case.forecasts[0].as_of <= arrival <= case.forecasts[0].end_on:
        raise ValueError('CHAT_ARRIVAL_OUTSIDE_HORIZON')
    # Recover admitted supply timing from the canonical calculation, not PO quantities.
    supplies = [SimpleNamespace(hospital_id=f.hospital_id, quantity=p.received,
                    modeled_arrival=p.day, shipment_id='saved-'+str(i),
                    evidence_id=f.evidence_ids[0])
                for f in case.forecasts for i, p in enumerate(f.points) if p.received]
    baseline = forecast_operations(case.operations, case.forecasts[0].as_of, supplies, case.dataset_version)
    if any([(p.unmet, p.closing_usable) for p in a.points] !=
           [(p.unmet, p.closing_usable) for p in b.points] for a, b in zip(baseline, case.forecasts)):
        raise ValueError('CHAT_SCENARIO_BASELINE_NOT_REPRODUCIBLE')
    extra = SimpleNamespace(hospital_id=hospital, quantity=bags, modeled_arrival=arrival,
                            shipment_id='hypothetical-arrival', evidence_id=case.evidence[0].evidence_id)
    return forecast_operations(case.operations, case.forecasts[0].as_of, supplies, case.dataset_version,
                               extra_arrivals={hospital: [extra]})


def classify(question, previous):
    text = question.lower()
    if any(w in text for w in ('what if', 'what happens', 'another', 'additional', 'hypothetical')):
        return 'scenario'
    if any(w in text for w in ('800', '500', 'received', 'shipped', 'reconcil', 'receipt discrepancy')):
        return 'reconciliation'
    if any(w in text for w in ('mitigat', 'option', 'alternative', 'transfer')):
        return 'mitigation'
    if any(w in text for w in ('nemotron', 'nightshift', 'agent', 'memory', 'h-mem', 'zetaris', 'sponsor')):
        return 'agents'
    if any(w in text for w in ('hospital', 'shortage', 'forecast', 'saline')):
        return 'forecast'
    if any(w in text for w in ('baxter', 'north cove', 'happened', 'manufacturer', 'helene')):
        return 'manufacturer'
    if any(w in text for w in ('why', 'that', 'those', 'it', 'more', 'source', 'evidence')):
        return previous or 'evidence'
    return 'evidence'


def coordinate(service, case, request, previous):
    topic = 'scenario' if request.scenario else classify(request.question, previous)
    executions = [ChatExecution(tool='helene_store.get', summary='Validated selected immutable snapshot.')]
    references = []

    def cite(key, label):
        source(service, case, key)  # Validate resolvability before returning any citation.
        references.append(ChatCitation(reference=key, label=label,
            href='/api/v1/helene/cases/'+quote(case.investigation_id, safe='')+'/chat-sources/'+key))
        executions.append(ChatExecution(tool='canonical.'+key+'.read', summary=label))

    names = {h.hospital_id: h.name.replace(' (fictional)', '') for h in case.operations.hospitals}
    if topic == 'manufacturer':
        cite('manufacturer', 'Baxter source documents & allocation notices')
        lines = ['The saved Hurricane Helene investigation concerns disruption at Baxter North Cove. '
                 'The retained manufacturer documents and extracted allocation notices are:']
        text = next((manufacturer_extract(s) for s in case.admission.sources
                     if str(s.issue_date) == '2024-09-29'), None)
        if text and all(word in text.lower() for word in ('flooding', 'closed for production', 'roadway collapse')):
            lines.insert(0, 'Baxter’s September 29 letter reports flooding inside North Cove, damage to inventory '
                         'and raw materials, and collapsed access roads. Production was closed while damage '
                         'was assessed. This is a paraphrase of the hash-verified retained manufacturer letter '
                         '(page 1), not a new live observation.')
        for s in case.admission.sources:
            notices = [a for a in case.admission.allocations if a.source_revision_id == s.revision_id]
            lines.append(f'{s.publisher}, {s.issue_date}: '+('; '.join(
                         f'{a.allocation_percent}% allocation for {a.container_ml:g} mL bags '
                         f'(page {a.page}; {a.kind}; effective {a.effective_on})' for a in notices)
                or 'Retained source document; inspect original for facility details.'))
        lines.append('These notices constrain supply expectations. Allocation percentages are not shipment or receipt counts. '
                     'Historical byte availability remains reconstructed; no reopening date or clinical outcome is inferred.')
    elif topic == 'forecast':
        cite('forecast', 'Canonical fourteen-day hospital forecasts')
        lines = ['Greatest projected unmet demand, ranked from this saved canonical forecast:']
        for f in sorted(case.forecasts, key=lambda f: -f.total_unmet):
            after = next(m.total_unmet for m in case.mitigated if m.hospital_id == f.hospital_id)
            lines.append(f'{names[f.hospital_id]}: {f.total_unmet} unmet bags; {after} after evaluated mitigation. '
                         f'Usable starting stock {f.initial_usable} bags; demand {f.expected_daily_demand} bags/day; '
                         f'first unmet demand {f.projected_stockout_date or "none within horizon"}.')
        lines.append('This is a calculated fictional hospital network, not an observed clinical shortage. '
                     'Conservative operational forecasts can differ because unconfirmed orders are excluded.')
    elif topic == 'mitigation':
        cite('mitigation', 'Evaluated alternatives, safety gates & review drafts')
        lines = ['The existing deterministic feasibility evaluator recorded these options:']
        for c in case.candidates:
            state = 'Rejected' if not c['feasible'] else (
                'Selected for human review' if c['candidate_id'] in case.selected else 'Feasible, not selected')
            lines.append(f"{c['kind'].replace('_', ' ')}: {c['quantity']} bags from "
                         f"{names.get(c.get('source_id'), 'external supplier')} to {names[c['hospital_id']]}. "
                         f"{state}; {c['remaining_unmet']} unmet bags remain. "
                         + ('Reasons: '+', '.join(c['failures']) if c['failures'] else 'Passed recorded feasibility checks.'))
        lines.append('These are review-only proposals; no transport, dispensing or purchase is authorized.')
    elif topic == 'reconciliation':
        cite('reconciliation', 'Claim reconciliation & receiving evidence')
        record = source(service, case, 'reconciliation')['verification']
        claims = record.get('claims', [])
        relevant = [c for c in claims if c.get('meaning') in ('shipped', 'accepted_receipt', 'shipment', 'dispatch')]
        lines = ['Dispatch claims and accepted physical receipts represent different events. '
                 'Only supported, accepted post-snapshot receiving evidence may be credited to inventory.']
        if relevant:
            for c in relevant[:12]:
                lines.append(f"{c.get('meaning')}: {c.get('quantity')} {c.get('unit', 'bag')}; "
                             f"record {c.get('record_id')}; status {c.get('status', c.get('assertion', 'see evidence'))}.")
        else:
            lines.append('This selected checkpoint does not contain an accepted 800-shipped / 500-received receiving pair. '
                         'I cannot assert that discrepancy for this investigation. Inspect the reconciliation source '
                         'or select the saved shipped-versus-received case in Evidence.')
        lines.append('An arithmetic difference of 300 units alone does not establish loss, theft or an additional receipt.')
    elif topic == 'agents':
        cite('agents', 'Saved Nightshift, Zetaris and H-MEM execution evidence')
        status = read_status(service, case.investigation_id)
        if status.available:
            lines = [f'This snapshot has {status.roles_executed} saved model-tool-model executions and '
                     f'{status.findings_validated} validated findings across {status.model_requests} requests. '
                     f'Model: {status.model}. This conversation executed zero model requests.',
                     'Hospital remains rejected; its malformed finding was excluded from downstream synthesis. '
                     'The closed verification run is not reopened.',
                     f"Zetaris mode: {status.data_layer.get('mode')}; returned rows: "
                     f"{status.data_layer.get('returned_rows', 'unreported')}. Application trace: "
                     f"{status.data_layer.get('trace_id', 'unreported')}.",
                     f"H-MEM writes: {len(status.hmem.get('writes', []))}; recalled records: "
                     f"{len(status.hmem.get('result', {}).get('memories', []))}. "
                     'Recalled rejected candidates constrain downstream selection.']
        else:
            lines = ['No saved native model receipts are available for this investigation. '
                     'Its deterministic tools are available in the source. No live integration success is inferred.']
    elif topic == 'scenario':
        cite('forecast', 'Baseline forecast and admitted inventory inputs')
        spec = request.scenario
        match = re.search(r'\b(\d{1,5})\s*(?:bags?|units?)\b', request.question.lower())
        if not spec and not match:
            lines = ['Specify an additional quantity in bags, a hospital and an arrival date within the forecast horizon. '
                     'No scenario was computed and no canonical record changed.']
        else:
            bags = spec.additional_bags if spec else int(match[1])
            if not 1 <= bags <= 10000:
                raise ValueError('CHAT_SCENARIO_QUANTITY_OUT_OF_BOUNDS')
            hospital = spec.hospital_id if spec else next((k for k, n in names.items()
                if k in request.question.lower() or n.lower() in request.question.lower()),
                max(case.forecasts, key=lambda f: f.total_unmet).hospital_id)
            parsed = re.search(r'\b\d{4}-\d{2}-\d{2}\b', request.question)
            arrival = spec.arrival_on if spec else (date.fromisoformat(parsed[0]) if parsed else case.forecasts[0].as_of)
            result = scenario(case, bags, hospital, arrival)
            executions.append(ChatExecution(tool='inventory.fefo.scenario', summary='Reproduced baseline, computed isolated hypothetical arrival.'))
            original = next(f for f in case.forecasts if f.hospital_id == hospital)
            changed = next(f for f in result if f.hospital_id == hospital)
            lines = [f'Hypothetical calculation: {bags} additional bags arrive at {names[hospital]} on {arrival}. '
                     'Assumptions: released stock, no reservations on the new lot, 30-day arrival shelf life, '
                     'unchanged demand and all other canonical inputs. '
                     + ('Facility/date default to the highest-shortage hospital and forecast start when unspecified. ' if not spec else ''),
                     f'Unmet demand changes from {original.total_unmet} to {changed.total_unmet} bags; '
                     f'the network total becomes {sum(f.total_unmet for f in result)} bags. '
                     'The shared FEFO engine recomputed every day; this is not a subtraction shortcut. '
                     'No canonical stock, forecast, mitigation or action was changed.']
    else:
        cite('evidence', 'Investigation evidence register')
        lines = ['I can retrieve the selected investigation’s manufacturer notices, ranked shortages, '
                 'receiving reconciliation, mitigation alternatives and saved specialist receipts, '
                 'or calculate a hypothetical arrival. I cannot answer that question reliably from the available '
                 'evidence. Rephrase around one of those topics; no live model executed.']
    # Always retrieve the relevant saved specialist context, without treating it as fresh inference.
    if topic != 'agents':
        saved = read_status(service, case.investigation_id)
        executions.append(ChatExecution(tool='specialists.saved.read', summary=(
            f'{saved.findings_validated} accepted saved findings; rejected findings excluded.'
            if saved.available else 'No saved native receipts for this snapshot.')))
    return topic, HeleneChatTurn(question=request.question, answer='\n\n'.join(lines),
        citations=references, executions=executions, limitations=LIMITATIONS)


def answer(service, identity, request: HeleneChatRequest):
    # No HTTP request, even one carrying a budget or mode flag, grants live inference.
    if request.mode != 'offline':
        raise ValueError('CHAT_LIVE_AUTHORIZATION_REQUIRED')
    case = service.helene_store.get(identity)
    initialize(service)
    conversation = request.conversation_id or 'conversation-'+uuid4().hex
    with service.store.connect() as db:
        row = db.execute('SELECT case_id,payload,topic FROM helene_conversations WHERE id=?', (conversation,)).fetchone()
        if request.conversation_id and not row:
            raise KeyError('CHAT_CONVERSATION_NOT_FOUND')
        if row and row[0] != identity:
            raise ValueError('CHAT_CONVERSATION_CASE_MISMATCH')
        turns = [HeleneChatTurn.model_validate(t) for t in json.loads(row[1])] if row else []
        if len(turns) >= 20:
            raise ValueError('CHAT_CONVERSATION_LIMIT')
        topic, turn = coordinate(service, case, request, row[2] if row else None)
        reply = HeleneChatResponse(investigation_id=identity, conversation_id=conversation, turns=[*turns, turn])
        payload = json.dumps([t.model_dump(mode='json') for t in reply.turns])
        if len(payload.encode()) > 128000:
            raise ValueError('CHAT_RESPONSE_BUDGET_EXCEEDED')
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT case_id,payload,topic FROM helene_conversations WHERE id=?', (conversation,)).fetchone()
        if current != row:
            raise ValueError('CHAT_CONVERSATION_CHANGED')
        db.execute('INSERT INTO helene_conversations VALUES (?,?,?,?) ON CONFLICT(id) '
                   'DO UPDATE SET payload=excluded.payload,topic=excluded.topic',
                   (conversation, identity, payload, topic))
    return reply


def history(service, identity, conversation):
    service.helene_store.get(identity)
    initialize(service)
    with service.store.connect() as db:
        row = db.execute('SELECT case_id,payload FROM helene_conversations WHERE id=?', (conversation,)).fetchone()
    if not row or row[0] != identity:
        raise KeyError('CHAT_CONVERSATION_NOT_FOUND')
    return HeleneChatResponse(investigation_id=identity, conversation_id=conversation,
                              turns=json.loads(row[1]))
