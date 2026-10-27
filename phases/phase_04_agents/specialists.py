"""Three specialists execute typed read plans; quantities always come from RiskTools."""
from .models import Finding, TaskResult


def finding(tools, key, value, kind, ids, *, analyses=(), limits=()):
    return Finding(claim_key=key, value=value, kind=kind, evidence_ids=sorted(set(ids)),
                   source_ids=sorted({tools.registry[i].source for i in ids}),
                   analysis_ids=list(analyses), limitations=list(limits))


def trade(tools, *, followup=False):
    rows, excluded = tools.query('trade', {'collection': 'trade_flows', 'limit': 100})
    if not rows:
        return TaskResult(role='trade', status='unavailable', missing=['trade_at_cutoff'])
    try:
        risk = tools.calculate('trade', 'risk.trade_exposure', {
            'context_as_of': tools.request.evidence_as_of.date().isoformat()})
    except ValueError:
        return TaskResult(role='trade', status='unavailable', missing=['comparable_trade_scope'],
                          error_code='TRADE_SCOPE_UNAVAILABLE')
    facts = [finding(tools, 'trade.sample',
        f'HS{risk.hs_code} {risk.period}: sample USD {risk.observed_import_value_usd}; ' +
        '; '.join(f'{c.country_iso} sample share {c.observed_sample_share:.6%}, national risk '
                  f'{c.country_rating.level or "Unclassified"}' for c in risk.countries),
        'derived', risk.input_evidence_ids, analyses=[risk.analysis_id], limits=risk.limitations)]
    if followup:
        facts.append(finding(tools, 'trade.denominator',
            'No world-partner record in the retrieved immutable sample; national shares, HHI and '
            'historical change remain unknown.', 'derived', risk.input_evidence_ids))
    return TaskResult(role='trade', status='complete', findings=facts,
                      missing=['national_denominator', 'prior_period'] + (['excluded_future_evidence'] if excluded else []),
                      calculations=[risk.model_dump(mode='json')])


def disruption(tools, *, followup=False):
    events, excluded = tools.query('disruption', {'collection': 'events', 'limit': 100})
    products, ep = tools.query('disruption', {'collection': 'products', 'limit': 100})
    shortages, es = tools.query('disruption', {'collection': 'shortages', 'limit': 100})
    facts = []
    for event in events:
        facts.append(finding(tools, 'event.' + event.event_id,
            f'{event.title}; established: {event.established}; uncertain: {event.uncertain_implications}',
            event.evidence.kind, [event.evidence.evidence_id], limits=[
                'A storm assessment does not establish a carboplatin manufacturing dependency.']))
    if shortages:
        label = 'Simulated fixture: ' if tools.request.mode == 'fixture' else ''
        real_products = [r for r in products if r.evidence.kind != 'simulated']
        ids = [r.evidence.evidence_id for r in shortages + real_products]
        facts.append(finding(tools, 'fda.retrieved_sample',
            f'{label}Retrieved {len(shortages)} shortage package records and {len(real_products)} non-simulated product projections. '
            'These source revisions establish their stated package availability only; '
            'they do not link the fictional suppliers or hospitals to Rocky Mount.', 'derived', ids,
            limits=['Bounded cached snapshot, not all FDA records or a historical revision archive.']))
    if followup and events:
        real_events = [e for e in events if e.evidence.kind != 'simulated']
        named = [e.event_id for e in events if e.evidence.kind != 'simulated'
                 and any('carboplatin' in p.lower() for p in e.products_stated)]
        facts.append(finding(tools, 'event.carboplatin_link',
            ('No documented carboplatin manufacturing link in retrieved event product statements.'
             if real_events else 'Observed event revisions unavailable at cutoff; manufacturing link unverified.') if not named
            else 'Product mentions require further source review; a manufacturing dependency remains unverified.',
            'derived', [e.evidence.evidence_id for e in events],
            limits=['Absence in the retrieved sources is not proof of absence. Synthetic Atlas hold is separate.']))
    return TaskResult(role='disruption', status='complete' if facts else 'unavailable', findings=facts,
        missing=['manufacturing_link'] + (['excluded_future_evidence'] if excluded + ep + es else []))


def hospital(tools):
    rows = {}
    for collection in ('suppliers', 'purchase_orders', 'shipments', 'inventory', 'hospitals',
                       'consumption', 'approvals'):
        rows[collection], _ = tools.query('hospital', {'collection': collection, 'limit': 100})
    if len(rows['hospitals']) != len(tools.bundle.hospitals) or not rows['inventory']:
        return TaskResult(role='hospital', status='unavailable', missing=['hospital_inputs_at_cutoff'])
    facts, calculations = [], []
    for h in rows['hospitals']:
        comparison = tools.calculate('hospital', 'risk.scenario_comparison', {
            'hospital_id': h.hospital_id, 'horizon_days': tools.request.horizon_days})
        b, d = comparison.baseline.forecast, comparison.disruption.forecast
        key = f'hospital.{h.hospital_id}.horizon-{tools.request.horizon_days}.original-phase2'
        facts.append(finding(tools, key,
            f'{h.hospital_id}: baseline unmet={b.total_unmet} vials; disruption unmet={d.total_unmet} vials; '
            f'incremental unmet={comparison.incremental_unmet}; baseline risk={comparison.baseline.rating.level}; '
            f'disruption risk={comparison.disruption.rating.level}; first unmet baseline={b.projected_stockout_date}, '
            f'disruption={d.projected_stockout_date}. {comparison.causal_explanation}',
            'derived', comparison.input_evidence_ids, analyses=[comparison.analysis_id],
            limits=[*comparison.limitations, 'All supplier/order/shipment/hospital dependencies are simulated.']))
        calculations.append(comparison.model_dump(mode='json'))
    return TaskResult(role='hospital', status='complete', findings=facts, calculations=calculations)


SPECIALISTS = {'trade': trade, 'disruption': disruption, 'hospital': hospital}
