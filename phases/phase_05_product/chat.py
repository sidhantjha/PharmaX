"""Deterministic Ask PharmaX tool router; never a replayed live model conversation."""
import re
from contracts.operator import ChatResponse, CaseRequest
from .reports import forecasts


def answer(service, request):
    if request.execution_mode == 'live':
        from .model_chat import live_answer
        return live_answer(service, request)
    case = service.store.get(request.investigation_id) if request.investigation_id else service.create(CaseRequest(scenario=request.scenario))
    question = request.question.lower()
    decisions=[]
    references=[]
    if re.search(r'(three|3) days? (later|delay)',question):
        if case.scenario != 'red-sea':
            text='Delivery sensitivity is unavailable for the historical hold scenario. Select the October route pilot.'
        else:
            # "three days later" is relative to the current selected arrival assumption.
            delay=case.intelligence.selected_delay_days+3
            if delay>21:
                text='Requested delay exceeds the supported 21-day sensitivity bound.'
            else:
                parent=case.investigation_id
                case=service.create(CaseRequest(scenario='red-sea',delay_days=delay,parent_id=parent))
                text=f'Created versioned scenario {case.investigation_id}: delay +{delay} days from scheduled arrival. '+case.mitigation.recommendation
                decisions.append({'tool':'investigation.create','arguments':{'parent_id':parent,'delay_days':delay},'result':case.investigation_id})
    elif any(w in question for w in ('draft','transfer request','purchase order')):
        actions=service.store.actions(case.investigation_id)
        kind='purchase_order' if 'purchase' in question else 'stock_transfer'
        selected=next((a for a in actions if a.kind==kind),None)
        text=selected.draft if selected else 'No feasible selected mitigation: an action draft cannot be prepared.'
        references=[f'/api/v1/investigations/{case.investigation_id}/actions'] if selected else []
        decisions.append({'tool':'actions.read','arguments':{'kind':kind},'result':selected.action_id if selected else 'unavailable'})
    elif any(w in question for w in ('patient','session','clinical')):
        impacts=[i for i in case.clinical_impact if i.case in ('disruption','mitigation')]
        text='Synthetic scheduling only: '+ '; '.join(f'{i.case} {i.hospital_id}: {i.unique_exposed_patients} unique modeled patients / {i.exposed_sessions} exposed sessions' for i in impacts)+'. These are not real patient outcomes or verified clinical doses.'
        references=[f'/api/v1/investigations/{case.investigation_id}/clinical-impact']
    elif any(w in question for w in ('credible','credibility','source','news','caused')):
        text=' '.join(f'{v.check}: {v.explanation}' for v in case.verification)
        if case.intelligence:
            text=case.intelligence.signal.title+'. '+text
        references=[f'/api/v1/investigations/{case.investigation_id}/evidence']
    elif any(w in question for w in ('reject','option','alternative')):
        text='; '.join(f'{e.candidate.candidate_id}: '+', '.join(f'{f.code}: {f.detail}' for f in e.failures)
                       for e in case.mitigation.evaluations if not e.feasible) or 'No rejected options recorded.'
    elif any(w in question for w in ('risk','exposed','hospital','inventory','run out','shortage')):
        rows=[(label,f) for label,f in forecasts(case) if label in ('disruption','mitigation','scheduled-arrival')]
        context=(f'Conditional route exposure to {case.intelligence.signal.title}; explicit assumed +{case.intelligence.selected_delay_days}-day delivery delay. An advisory does not prove a shipment delay. ' if case.intelligence else
                 'Synthetic shipment hold with independently constrained starting inventory; no observed manufacturing link asserted. ')
        text=context+'; '.join(f'{label} {f.request.hospital_id}: {f.total_unmet} unmet vials; first unmet {f.projected_stockout_date}' for label,f in rows)
        text+='. '+(case.mitigation.recommendation or '')
        references=[f'/api/v1/investigations/{case.investigation_id}/forecast']
    else:
        text='Unavailable: this deterministic router supports source credibility, risk/stockout, hospitals, synthetic patient impact, rejected options, three-days-later sensitivity, and action drafts. No model inference was performed.'
    decisions.append({'tool':'case.read','arguments':{'investigation_id':case.investigation_id},
                      'result':case.status,'execution':'deterministic-local-router'})
    response=ChatResponse(investigation_id=case.investigation_id,answer=text,citations=case.evidence,
        decisions=decisions,references=references,limitations=case.limitations)
    service.save_chat(request,response)
    return response
