"""Separate October 2026 simulated network; never edits or reloads July replay data."""
from datetime import date, datetime, time, timedelta, timezone
from contracts import (DataBundle, SourceEvidence, SupplyDependency, RouteLeg, GeoBox,
                       MitigationCatalog, MitigationCandidate, SupplyRoute, SupplierStock)
from phases.phase_01_foundation.sources import digest
from phases.phase_02_data.scenario import build_scenario
from phases.phase_04_agents.mitigation_scenario import seal, load_catalog


def simulated(label, body, anchor, parents=()):
    identity = digest({'label': label, 'body': body, 'anchor': anchor.isoformat(), 'parents': sorted(parents)})
    stamp = datetime.combine(anchor, time.min, tzinfo=timezone.utc)
    return SourceEvidence(evidence_id='sim:p4c-' + identity, kind='simulated', source='fictional-october-2026-v1',
        source_record_id=label, source_url='https://example.invalid/pharmax/october-2026-v1',
        observed_at=stamp, version_available_at=stamp, content_sha256=digest(body), parent_ids=sorted(parents),
        note='Hypothetical planning input known at scenario start, not an observed historical record or actual route.')


def shift(body, delta):
    return {k: (v + delta).isoformat() if isinstance(v, date) else v for k, v in body.items()}


def build_current(anchor, cutoff):
    if (anchor.year, anchor.month) != (2026, 10) or datetime.combine(anchor, time.min, tzinfo=timezone.utc) > cutoff:
        raise ValueError('October scenario must be on/before actual evidence retrieval')
    delta = anchor - date(2023, 7, 24)
    groups, records = build_scenario(), {}
    root = simulated('october-2026-v1', {'anchor': str(anchor), 'description': 'Separate simulated October planning network'}, anchor)
    evs = [root]
    for collection, items in groups.items():
        if collection == 'events':
            records[collection] = []
            continue
        records[collection] = []
        for item in items:
            body = shift(item.model_dump(exclude={'evidence'}), delta)
            if collection == 'suppliers' and body['supplier_id'] == 'sim-atlas':
                body['name'] = 'Fictional Atlas Gujarat Pharma — October route scenario'
            if collection == 'inventory':
                body['expiry_date'] = str(anchor + timedelta(days=90))
                body['quality_status'] = 'released'
                body['on_hand'] = {'stock-central': 158, 'stock-north': 200, 'stock-south': 200}.get(body['inventory_id'], body['on_hand'])
            if collection == 'purchase_orders' and body['purchase_order_id'] == 'po-central':
                body.update(quantity=90, ordered_on=str(anchor-timedelta(days=10)), expected_delivery=str(anchor+timedelta(days=22)))
            if collection == 'shipments' and body['shipment_id'] == 'ship-central':
                body.update(quantity=90, status='in_transit', dispatched_on=str(anchor-timedelta(days=8)),
                            expected_arrival=str(anchor+timedelta(days=22)), disruption_event_ids=[])
            ev = simulated(collection, body, anchor, [root.evidence_id])
            evs.append(ev)
            records[collection].append(type(item).model_validate({**body, 'evidence': ev}))
    bundle = DataBundle(dataset_version='0'*64, mode='fixture', scenario_as_of=anchor, snapshot_ids=[],
        coverage=['Entirely simulated October network; no observed pharmaceutical route or cargo dependency.'],
        trade_flows=[], shortages=[], recalls=[], evidence=evs, **records)
    bundle = seal(bundle)
    specs = [
        ('arabian', 'Nhava Sheva, India', 'Arabian Sea', ['arabian-sea'], (55,12,74,23), -7,0),
        ('aden', 'Arabian Sea', 'Gulf of Aden', ['gulf-of-aden'], (43,10,52,16),0,1),
        ('red-sea', 'Bab el Mandeb', 'Northern Red Sea', ['bab-el-mandeb','red-sea'], (32,12,44,30),1,5),
        ('suez', 'Suez', 'Port Said', ['suez'], (32,29,33,32),6,6),
        ('mediterranean', 'Port Said', 'Gibraltar', ['mediterranean'], (-6,30,33,38),7,10),
        ('atlantic', 'Gibraltar', 'Port Newark, USA', ['north-atlantic'], (-75,30,-5,42),11,20),
        ('illinois', 'Port Newark', 'Fictional Central Illinois hospital', ['us-inland'], (-90,38,-74,42),21,22),
    ]
    legs = [RouteLeg(leg_id=k, mode='road' if k=='illinois' else 'sea', origin=a, destination=b, corridors=c,
        geography=GeoBox(west=box[0],south=box[1],east=box[2],north=box[3]),
        starts_on=anchor+timedelta(days=x),ends_on=anchor+timedelta(days=y)) for k,a,b,c,box,x,y in specs]
    dependency = SupplyDependency(scenario_id='october-2026-red-sea-v1', dataset_version=bundle.dataset_version,
        shipment_id='ship-central', supplier_id='sim-atlas', hospital_id='sim-central', product_id='sim-carboplatin-450',
        quantity_vials=90, planned_arrival=anchor+timedelta(days=22), legs=legs,
        assumptions=['Simulated temperature-controlled sea/inland journey from Nhava Sheva via Red Sea/Suez and Port Newark.',
            'Approximately thirty-day modeled voyage from dispatch to Illinois; legs, geography and transit times are assumptions.',
            'No source establishes a real carboplatin shipment, supplier exposure, carrier affiliation or actual delay.'])
    return bundle, dependency


def current_catalog(bundle):
    """Reuse typed Phase 4B proposals with separately dated, hashed operational assumptions."""
    anchor = bundle.scenario_as_of
    delta = anchor - date(2023, 7, 24)
    old = load_catalog()
    evs, stocks, routes, candidates = [], [], [], []
    sources = {s.inventory_id: s.evidence.evidence_id for s in bundle.inventory}
    sources.update({s.supplier_id: s.evidence.evidence_id for s in bundle.suppliers})
    sources.update({h.hospital_id: h.evidence.evidence_id for h in bundle.hospitals})

    def create(model, label, body, parents):
        ev = simulated(label, body, anchor, parents)
        evs.append(ev)
        return model.model_validate({**body, 'evidence': ev})

    for old_stock in old.supplier_stocks:
        body = shift(old_stock.model_dump(exclude={'evidence'}), delta)
        stocks.append(create(SupplierStock,'local-supplier-stock',body,[sources[old_stock.supplier_id]]))
    for old_route in old.routes:
        if old_route.route_id in ('route-atlas-release','route-south'):
            continue
        body = shift(old_route.model_dump(exclude={'evidence'}), delta)
        routes.append(create(SupplyRoute,'route',body,[sources[old_route.source_id]]))
    sources.update({s.stock_id:s.evidence.evidence_id for s in stocks})
    by_route = {r.route_id:r for r in routes}
    for c in old.candidates:
        if c.candidate_id in ('atlas-release','south-quarantined'):
            continue
        body = shift(c.model_dump(exclude={'evidence_ids'}), delta)
        if c.candidate_id == 'north-bulk':
            body['quantity'] = 180
        parents = [sources[c.source_id],by_route[c.route_id].evidence.evidence_id]
        ev = simulated('candidate',body,anchor,parents)
        evs.append(ev)
        candidates.append(MitigationCandidate.model_validate({**body,'evidence_ids':[ev.evidence_id,*parents]}))
    return MitigationCatalog(catalog_id='october-mitigation-v1',base_dataset_version=bundle.dataset_version,
        scenario_as_of=anchor,available_at=datetime.combine(anchor,time.min,tzinfo=timezone.utc),
        supplier_stocks=stocks,routes=routes,candidates=candidates,evidence=evs,
        assumptions=['All October inventories, permissions, arrival windows and interventions are simulated.',
            'Alternative local supply and same-day hospital transfer do not use the exposed ocean corridor.',
            'Alternatives are independent, not combined or physically executed; reservations and donor safety remain enforced.'])
