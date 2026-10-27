"""Explicit fictional single-line X12 transaction subset; no ISA/GS/partner claim.

Supported profiles: ST/BEG/REF/PO1/CTT/SE (850), ST/BAK/REF/ACK/SE (855),
ST/BSN/REF/SN1/SE (856). REF qualifiers are PO, LI, AC, HF and VN.
Only EA (one unopened bag) is accepted. Outputs still require normal CSV lineage
joins and cutoff admission. This is a subset preview, never an ERP connection.
"""
import re


def parse_subset(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 16384:
        raise ValueError('EDI_BYTE_BUDGET')
    segments = [s.strip().split('*') for s in raw.strip().rstrip('~').split('~')]
    try:
        if len(segments) > 16 or segments[0][0] != 'ST' or segments[-1][0] != 'SE':
            raise ValueError('EDI_ENVELOPE_UNSUPPORTED')
        kind, control = segments[0][1:]
        if segments[-1] != ['SE', str(len(segments)), control]:
            raise ValueError('EDI_SEGMENT_COUNT_OR_CONTROL')
        profiles = {'850': ('BEG', 'PO1'), '855': ('BAK', 'ACK'), '856': ('BSN', 'SN1')}
        header, line = profiles[kind]
        refs = {}
        for s in segments[2:-2]:
            if s[0] == 'REF':
                if len(s) != 3 or s[1] in refs:
                    raise ValueError('EDI_DUPLICATE_REFERENCE')
                refs[s[1]] = s[2]
        if set(refs) != {'PO', 'LI', 'AC', 'HF', 'VN'} or any(not v for v in refs.values()):
            raise ValueError('EDI_REFERENCE_PROFILE')
        expected = ['ST', header, *(['REF']*5), line, *(['CTT'] if kind == '850' else []), 'SE']
        if [s[0] for s in segments] != expected:
            raise ValueError('EDI_TRANSACTION_PROFILE')
        h = segments[1]
        item = segments[-3 if kind == '850' else -2]
        if kind == '850':
            if len(h) != 6 or h[1:3] != ['00', 'NE'] or h[3] != refs['PO'] or h[4] != '':
                raise ValueError('EDI_BEG_PROFILE')
            if len(item) != 9 or item[1] != refs['LI'] or item[3] != 'EA' or item[6:] != ['VN', refs['VN'], '']:
                raise ValueError('EDI_PO1_PROFILE')
            quantity, state, day = item[2], 'requested', h[5]
            if segments[-2] != ['CTT', '1']:
                raise ValueError('EDI_LINE_COUNT')
        elif kind == '855':
            if len(h) != 5 or h[1:3] != ['00', 'AC'] or h[3] != refs['PO'] or len(item) != 4 or item[1] not in {'IA', 'IB'} or item[3] != 'EA':
                raise ValueError('EDI_ACK_PROFILE')
            quantity, state, day = item[2], 'accepted' if item[1] == 'IA' else 'backordered', h[4]
        else:
            if len(h) != 5 or h[1] != '00' or not h[2] or h[4] != '0000' or len(item) != 4 or item[1] != refs['LI'] or item[3] != 'EA':
                raise ValueError('EDI_SHIPMENT_PROFILE')
            quantity, state, day = item[2], 'supplier_notice_only', h[3]
        if not re.fullmatch(r'[0-9]+', quantity) or not re.fullmatch(r'[0-9]{8}', day):
            raise ValueError('EDI_QUANTITY_OR_DATE')
        from datetime import datetime
        effective = datetime.strptime(day, '%Y%m%d').date()
        return dict(profile='pharmax-synthetic-single-line-v1', transaction=kind,
            references=refs, quantity=int(quantity), unit='bag', state=state,
            effective_on=str(effective), classification='synthetic; requires CSV linkage validation')
    except (IndexError, KeyError):
        raise ValueError('EDI_TRANSACTION_PROFILE') from None
