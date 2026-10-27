"""Content-addressed, isolated scenario snapshots; original records stay immutable."""
from datetime import datetime, time, timezone
from pathlib import Path
from contracts import MitigationCatalog, ScenarioOverlay, SourceEvidence
from phases.phase_01_foundation.sources import digest
from phases.phase_02_data.validation import validate_bundle, version_for

HERE = Path(__file__).resolve().parent


def seal(bundle):
    value = bundle.model_dump(mode='json')
    value['dataset_version'] = version_for(value)
    return validate_bundle(value)


def simulated_evidence(bundle, label, payload, parents):
    stamp = datetime.combine(bundle.scenario_as_of, time.min, tzinfo=timezone.utc)
    identity = digest({'label': label, 'payload': payload, 'parents': sorted(set(parents))})
    return SourceEvidence(evidence_id='sim:p4b-' + identity, kind='simulated',
        source='pharmax-mitigation-projection-v1', source_record_id=label,
        source_url='https://example.invalid/pharmax/mitigation-projection-v1',
        observed_at=stamp, version_available_at=stamp, content_sha256=identity,
        parent_ids=sorted(set(parents)), note='Hypothetical scenario input, not observed operational evidence.')


def load_catalog(path=None):
    return validate_catalog(MitigationCatalog.model_validate_json(
        Path(path or HERE / 'fixtures/mitigation_options.json').read_text()))


def validate_catalog(value):
    catalog = MitigationCatalog.model_validate(value).model_copy(deep=True)
    registry = {e.evidence_id: e for e in catalog.evidence}
    if len(registry) != len(catalog.evidence) or any(e.kind != 'simulated' for e in catalog.evidence):
        raise ValueError('Invalid simulated catalog evidence')
    for field, key in (('supplier_stocks', 'stock_id'), ('routes', 'route_id'), ('candidates', 'candidate_id')):
        records = getattr(catalog, field)
        if len({getattr(r, key) for r in records}) != len(records):
            raise ValueError('Duplicate catalog identifier')
        for r in records:
            ev = r.evidence if hasattr(r, 'evidence') else registry[r.evidence_ids[0]]
            body = r.model_dump(mode='json', exclude={'evidence', 'evidence_ids'})
            if ev != registry[ev.evidence_id] or digest(body) != ev.content_sha256:
                raise ValueError('Catalog evidence hash mismatch')
    return catalog


def prepare_scenario(bundle, catalog, scenario, *, overlay=None):
    catalog = validate_catalog(catalog)
    base = validate_bundle(bundle).model_copy(deep=True)
    if catalog.base_dataset_version != base.dataset_version or catalog.scenario_as_of != base.scenario_as_of:
        raise ValueError('CATALOG_SCENARIO_MISMATCH')
    if scenario == 'clean-onset' and overlay is None:
        overlay = ScenarioOverlay.model_validate_json((HERE.parent / 'phase_03_risk/fixtures/demo_overlay.json').read_text())
    if overlay:
        from phases.phase_03_risk.shipments import validate_overlay
        validate_overlay(base, overlay)
        if overlay.arrivals:
            raise ValueError('Only the versioned starting-stock demo overlay is supported')
        changes = {s.inventory_id: s for s in overlay.stock}
        for index, stock in enumerate(base.inventory):
            if stock.inventory_id in changes:
                change = changes[stock.inventory_id]
                ev = simulated_evidence(base, overlay.overlay_id, overlay.model_dump(mode='json'), [stock.evidence.evidence_id])
                base.evidence.append(ev)
                base.inventory[index] = stock.model_copy(update={'on_hand': change.on_hand,
                    'reserved': stock.reserved if change.reserved is None else change.reserved, 'evidence': ev})
    known = {e.evidence_id for e in base.evidence}
    for ev in catalog.evidence:
        if ev.evidence_id in known:
            raise ValueError('Catalog evidence collides with base')
        base.evidence.append(ev.model_copy(deep=True))
        known.add(ev.evidence_id)
    return seal(base), overlay
