import type { ActionDraft, WorkspaceCase, WorkspaceForecast, WorkspaceData } from './operatorWorkspace';
/** Checked projections of contracts/operator.py and the exported 5a.1 schemas.
 * Unknown backend fields are preserved, but every field rendered here is checked.
 * Default same-origin URL works through Vite or a production reverse proxy.
 */
export interface Page<T> {
  schema_version: '5a.1'; resource: string; investigation_id: string | null;
  as_of: string; availability: 'available' | 'unavailable'; reason: string | null;
  offset: number; limit: number; total: number; items: T[];
}
export interface Health { status: 'ok'; checked_at: string; model_runtime: string; network: string }
export interface CaseSummary {
  investigation_id: string; scenario: string; created_at: string;
  status: string; parent_id: string | null;
}
export interface Evidence {
  evidence_id: string; kind: 'observed' | 'derived' | 'simulated'; source: string;
  source_url: string; observed_at: string; content_sha256: string;
}
export interface Investigation extends CaseSummary {
  schema_version: '5a.1'; mode: string; model_execution: 'deterministic-local' | 'nightshift';
  objective: string; limitations: string[];
  medicines: { product_id: string; name: string; presentation: string; quantity_unit: 'vial' }[];
  hospitals: { hospital_id: string; name: string }[];
  evidence: Evidence[];
  agents: { agent_id: string; status: string; execution: string; explanation: string; evidence_ids: string[]; memory_ids: string[] }[];
  mitigation: { recommendation_id: string | null; recommendation: string | null;
    evaluations: { evaluation_id: string; feasible: boolean; remaining_unmet: number;
      candidate: { candidate_id: string }; failures: { code: string; detail: string }[] }[] };
  revision: { decision_summary: string; disposition: string; parent_id: string } | null;
  actions: { action_id: string; kind: string; status: string; quantity: number; quantity_unit: 'vial'; dispatch_available: boolean }[];
  clinical_impact: { case: string; hospital_id: string; unique_exposed_patients: number; classification: string }[];
}
export interface ForecastSeries {
  case: 'baseline' | 'disruption' | 'mitigation';
  forecast: { request: { hospital_id: string; product_id: string }; dependency_kind: 'simulated';
    analysis_id: string; initial_usable: number; total_unmet: number; projected_stockout_date: string | null;
    points: { day: string; closing_usable: number; unmet: number }[] };
}
export class OperatorApiError extends Error {
  code: string; status?: number;
  constructor(code: string, status?: number) { super(code); this.name = 'OperatorApiError'; this.code = code; this.status = status; }
}
function fail(): never { throw new OperatorApiError('INVALID_API_RESPONSE'); }
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function text(value: unknown): asserts value is string { if (typeof value !== 'string' || !value.length) fail(); }
function nullableText(value: unknown) { if (value !== null) text(value); }
function count(value: unknown): asserts value is number { if (!Number.isSafeInteger(value) || (value as number) < 0) fail(); }
function boolean(value: unknown) { if (typeof value !== 'boolean') fail(); }
function oneOf(value: unknown, allowed: readonly string[]) { if (!allowed.includes(value as string)) fail(); }
function timestamp(value: unknown) { text(value); if (!/(Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) fail(); }
function array(value: unknown, check: (item: unknown) => void) {
  if (!Array.isArray(value)) fail(); value.forEach(check);
}
function summary(value: unknown) {
  const v = object(value); text(v.investigation_id); text(v.scenario); timestamp(v.created_at);
  oneOf(v.status, ['complete', 'incomplete', 'blocked']); nullableText(v.parent_id);
}
function investigation(value: unknown) {
  summary(value); const v = object(value);
  oneOf(v.schema_version, ['5a.1']);
  oneOf(v.mode, ['offline-replay', 'live-source', 'retained-source-live-model']);
  oneOf(v.model_execution, ['deterministic-local', 'nightshift']); text(v.objective); array(v.limitations, text);
  array(v.medicines, item => { const m = object(item); text(m.product_id); text(m.name); text(m.presentation); oneOf(m.quantity_unit, ['vial']); });
  array(v.hospitals, item => { const h = object(item); text(h.hospital_id); text(h.name); });
  array(v.evidence, item => {
    const e = object(item); text(e.evidence_id); text(e.source); text(e.source_url); timestamp(e.observed_at);
    oneOf(e.kind, ['observed', 'derived', 'simulated']); text(e.content_sha256);
    if (!/^[a-f0-9]{64}$/.test(e.content_sha256)) fail();
  });
  array(v.agents, item => { const a = object(item); text(a.agent_id); text(a.status); text(a.execution);
    text(a.explanation); array(a.evidence_ids, text); array(a.memory_ids, text); });
  const mitigation = object(v.mitigation); nullableText(mitigation.recommendation_id); nullableText(mitigation.recommendation);
  array(mitigation.evaluations, item => {
    const e = object(item); text(e.evaluation_id); boolean(e.feasible); count(e.remaining_unmet);
    text(object(e.candidate).candidate_id); array(e.failures, f => { const r = object(f); text(r.code); text(r.detail); });
  });
  if (v.revision !== null) { const r = object(v.revision); text(r.decision_summary); text(r.disposition); text(r.parent_id); }
  array(v.actions, item => { const a = object(item); text(a.action_id); text(a.kind); text(a.status);
    count(a.quantity); oneOf(a.quantity_unit, ['vial']); boolean(a.dispatch_available); });
  array(v.clinical_impact, item => { const c = object(item); text(c.case); text(c.hospital_id);
    count(c.unique_exposed_patients); text(c.classification); });
}
function forecast(value: unknown) {
  const s = object(value); oneOf(s.case, ['baseline', 'disruption', 'mitigation']); const f = object(s.forecast);
  const r = object(f.request); text(r.hospital_id); text(r.product_id); oneOf(f.dependency_kind, ['simulated']);
  text(f.analysis_id); count(f.initial_usable); count(f.total_unmet); nullableText(f.projected_stockout_date);
  array(f.points, item => { const p = object(item); text(p.day); count(p.closing_usable); count(p.unmet); });
}
function page<T>(value: unknown, resource: string, check: (item: unknown) => void, identity: string | null = null): Page<T> {
  const v = object(value); oneOf(v.schema_version, ['5a.1']);
  if (v.resource !== resource || v.investigation_id !== identity) fail();
  timestamp(v.as_of); oneOf(v.availability, ['available', 'unavailable']); nullableText(v.reason);
  count(v.offset); count(v.limit); count(v.total); array(v.items, check);
  if (v.limit < 1 || v.limit > 100 || (v.items as unknown[]).length > v.limit || (v.items as unknown[]).length > v.total) fail();
  if (v.availability === 'unavailable') throw new OperatorApiError('RESOURCE_UNAVAILABLE');
  return value as Page<T>;
}
function bounds(value: unknown) {
  const b = object(value);
  for (const key of ['west','east','south','north']) if (typeof b[key] !== 'number' || !Number.isFinite(b[key])) fail();
  if (Number(b.west) < -180 || Number(b.east) > 180 || Number(b.south) < -90 || Number(b.north) > 90 || Number(b.west) > Number(b.east) || Number(b.south) > Number(b.north)) fail();
}
function actionDraft(value: unknown) {
  const a = object(value);
  for (const key of ['action_id','investigation_id','mitigation_id','kind','status','product_id','source_organization','receiving_organization','target_on','justification','expected_benefit','draft']) text(a[key]);
  count(a.quantity); count(a.revision); oneOf(a.quantity_unit, ['vial']);
  if (a.dispatch_available !== false) fail();
  array(a.required_authorizations, text); array(a.outstanding_verification, text);
}
function workspaceCase(value: unknown) {
  investigation(value); const c = object(value); const m = object(c.mitigation);
  const r = object(m.request); text(r.hospital_id); text(r.product_id); array(m.memory_ids, text);
  array(c.hospitals, value => { const h=object(value); text(h.state); text(h.country_iso); });
  array(m.evaluations, value => {
    const e=object(value), candidate=object(e.candidate);
    for (const key of ['kind','source_id','hospital_id','product_id','presentation','arrival_on']) text(candidate[key]);
    count(candidate.quantity); array(candidate.evidence_ids,text); count(e.original_unmet); count(e.quantity_moved);
    if(e.simulated!==true || e.requires_human_review!==true) fail();
    array(e.evidence_ids,text); array(e.analysis_ids,text);
    if(e.supply_balance!==null){ const b=object(e.supply_balance); count(b.available_before); count(b.available_after); count(b.debited); boolean(b.conserved); }
    array(e.impacts, value => { const i=object(value); text(i.hospital_id); boolean(i.donor); count(object(i.effective).unmet_vials); });
  });
  array(c.agents, value => { const a=object(value); text(a.responsibility); text(a.next_action); if(!Array.isArray(a.tool_records))fail(); });
  array(c.clinical_impact, value => { const a=object(value); count(a.exposed_sessions); nullableText(a.first_unmet_on); });
  array(c.decision_ledger, value => { const d=object(value); for(const key of ['record_id','agent_id','operation','status'])text(d[key]); count(d.sequence); nullableText(d.tool_identifier); oneOf(d.classification,['executed-operation','agent-recommendation']); array(d.evidence_ids,text); array(d.memory_ids,text); array(d.memory_refs,text); });
  if(c.intelligence!==null){ const i=object(c.intelligence), s=object(i.signal); text(s.title); array(s.geography,bounds); array(s.evidence_ids,text);
    array(object(i.dependency).legs, value => { const l=object(value); text(l.leg_id); text(l.origin); text(l.destination); bounds(l.geography); }); }
  if(c.revision!==null){ const r=object(c.revision); nullableText(r.previous_recommendation_id); array(r.prior_constraint_memory_refs,text);
    if(r.previous_option_recheck!==null){const p=object(r.previous_option_recheck);text(p.candidate_id);boolean(p.feasible);count(p.remaining_unmet);}
    array(r.forecast_deltas,value=>{const f=object(value);text(f.hospital_id);count(f.previous_unmet_vials);count(f.revised_unmet_vials);}); }
}
function workspaceForecast(value: unknown) {
  forecast(value); const f=object(object(value).forecast); text(f.as_of);text(f.end_on);
  const p=object(f.product_identity);text(p.presentation);count(p.total_mg);oneOf(p.quantity_unit,['vial']);
  array(f.initial_inventory_inputs,value=>{const i=object(value);count(i.on_hand);count(i.reserved);text(i.product_id);text(i.quality_status);if(Number(i.reserved)>Number(i.on_hand)||i.product_id!==object(f.request).product_id)fail();});
}
export function createOperatorClient({ baseUrl = '/api/v1', fetcher = (...args: Parameters<typeof fetch>) => globalThis.fetch(...args), timeoutMs = 15_000 } = {}) {
  const base = baseUrl.replace(/\/$/, '');
  async function read(path: string, signal?: AbortSignal, init: RequestInit = {}): Promise<unknown> {
    const controller = new AbortController(); const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true }); if (signal?.aborted) abort();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; abort(); }, timeoutMs);
    try {
      const response = await fetcher(base + path, { ...init, signal: controller.signal, headers: { Accept: 'application/json', ...(init.body ? {'Content-Type':'application/json'} : {}) } });
      if (!response.ok) throw new OperatorApiError('HTTP_ERROR', response.status);
      let value: unknown;
      try { value = await response.json(); } catch { return fail(); }
      return value;
    } catch (error) {
      if (error instanceof OperatorApiError) throw error;
      if (signal?.aborted) throw new OperatorApiError('CANCELLED');
      throw new OperatorApiError(timedOut ? 'TIMEOUT' : 'NETWORK_UNAVAILABLE');
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }
  return {
    async workspace(id: string, signal?: AbortSignal): Promise<WorkspaceData> {
      const resource='investigations/'+id, path='/investigations/'+encodeURIComponent(id);
      const [raw, forecastRaw, actionRaw] = await Promise.all([read(path,signal), read(path+'/forecast?limit=100',signal), read(path+'/actions?limit=100',signal)]);
      const snapshots=page<WorkspaceCase>(raw,resource,workspaceCase,id);
      const forecasts=page<WorkspaceForecast>(forecastRaw,resource+'/forecast',workspaceForecast,id);
      const actions=page<ActionDraft>(actionRaw,resource+'/actions',actionDraft,id);
      if(snapshots.items.length!==1 || snapshots.items[0].investigation_id!==id || forecasts.items.length!==forecasts.total || actions.items.length!==actions.total)fail();
      const snapshot=snapshots.items[0];
      if(actions.items.some(a=>a.investigation_id!==id) || forecasts.items.some(f=>!snapshot.hospitals.some(h=>h.hospital_id===f.forecast.request.hospital_id) || !snapshot.medicines.some(m=>m.product_id===f.forecast.request.product_id)))fail();
      if(!snapshot.hospitals.some(h=>h.hospital_id===snapshot.mitigation.request.hospital_id)||!snapshot.medicines.some(m=>m.product_id===snapshot.mitigation.request.product_id))fail();
      for(const h of snapshot.hospitals)for(const label of ['baseline','disruption','mitigation']){
        const matching=forecasts.items.filter(f=>f.forecast.request.hospital_id===h.hospital_id&&f.forecast.request.product_id===snapshot.mitigation.request.product_id&&f.case===label);
        if(matching.length!==1)fail();
      }
      return {snapshot,forecasts:forecasts.items,actions:actions.items};
    },
    async reviewAction(id: string, actionId: string, request: {expected_revision:number;status:'READY_FOR_REVIEW'|'APPROVED';operator:string;note:string;attestations:string[]}, signal?: AbortSignal) {
      const result=await read('/investigations/'+encodeURIComponent(id)+'/actions/'+encodeURIComponent(actionId)+'/review',signal,{method:'POST',body:JSON.stringify(request)});
      actionDraft(result); const action=result as ActionDraft;
      if(action.investigation_id!==id || action.action_id!==actionId || action.revision!==request.expected_revision+1 || action.status!==request.status)fail();
      return action;
    },
    async health(signal?: AbortSignal) {
      const result = page<Health>(await read('/health', signal), 'health', item => {
        const h = object(item); oneOf(h.status, ['ok']); timestamp(h.checked_at); text(h.model_runtime); text(h.network);
      });
      if (result.items.length !== 1 || result.total !== 1 || result.offset !== 0) fail();
      return result;
    },
    async investigations(offset = 0, signal?: AbortSignal) {
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > 100000) throw new OperatorApiError('INVALID_OFFSET');
      const result = page<CaseSummary>(await read(`/investigations?limit=20&offset=${offset}`, signal), 'investigations', summary);
      if (result.offset !== offset || result.limit !== 20) fail();
      return result;
    },
    async investigation(id: string, signal?: AbortSignal) {
      const resource = 'investigations/' + encodeURIComponent(id);
      const result = page<Investigation>(await read('/' + resource, signal), 'investigations/' + id, investigation, id);
      if (result.items.length !== 1 || result.items[0].investigation_id !== id) fail();
      return result.items[0];
    },
    async forecasts(id: string, signal?: AbortSignal) {
      const result = page<ForecastSeries>(await read('/investigations/' + encodeURIComponent(id) + '/forecast?limit=100', signal),
        'investigations/' + id + '/forecast', forecast, id);
      if (result.total !== result.items.length || result.offset !== 0) throw new OperatorApiError('INCOMPLETE_FORECAST');
      return result.items;
    },
  };
}
export type OperatorClient = ReturnType<typeof createOperatorClient>;
