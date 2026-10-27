import type { Investigation, ForecastSeries } from './operatorApi';

export interface Candidate {
  evaluation_id: string; feasible: boolean; original_unmet: number; remaining_unmet: number;
  quantity_moved: number; simulated: true; requires_human_review: true;
  candidate: { candidate_id: string; kind: string; source_id: string; hospital_id: string;
    product_id: string; presentation: string; quantity: number; arrival_on: string; evidence_ids: string[] };
  failures: { code: string; detail: string }[];
  supply_balance: { available_before: number; available_after: number; debited: number; conserved: boolean } | null;
  impacts: { hospital_id: string; donor: boolean; effective: { unmet_vials: number } }[];
  evidence_ids: string[]; analysis_ids: string[];
}
export interface ActionDraft {
  action_id: string; investigation_id: string; mitigation_id: string; kind: string; status: 'DRAFT' | 'READY_FOR_REVIEW' | 'APPROVED';
  quantity: number; quantity_unit: 'vial'; product_id: string; source_organization: string; receiving_organization: string;
  target_on: string; justification: string; expected_benefit: string; draft: string; revision: number;
  required_authorizations: string[]; outstanding_verification: string[]; dispatch_available: false;
}
export interface LedgerRecord {
  record_id: string; sequence: number; agent_id: string; operation: string; status: string;
  classification: 'executed-operation' | 'agent-recommendation'; tool_identifier: string | null;
  evidence_ids: string[]; memory_ids: string[]; memory_refs: string[];
}
export interface Bounds { west: number; south: number; east: number; north: number }
export type WorkspaceCase = Omit<Investigation, 'hospitals'|'mitigation'|'revision'|'agents'|'clinical_impact'> & {
  hospitals: (Investigation['hospitals'][number] & { state: string; country_iso: string })[];
  mitigation: Omit<Investigation['mitigation'],'evaluations'> & { request: { hospital_id: string; product_id: string }; evaluations: Candidate[]; memory_ids: string[] };
  intelligence: { signal: { title: string; geography: Bounds[]; evidence_ids: string[] };
    dependency: { legs: { leg_id: string; origin: string; destination: string; geography: Bounds }[] } } | null;
  revision: (NonNullable<Investigation['revision']> & { previous_recommendation_id: string | null;
    previous_option_recheck: { candidate_id: string; feasible: boolean; remaining_unmet: number } | null; prior_constraint_memory_refs: string[];
    forecast_deltas: { hospital_id: string; previous_unmet_vials: number; revised_unmet_vials: number }[] }) | null;
  decision_ledger: LedgerRecord[];
  agents: (Investigation['agents'][number] & { responsibility: string; next_action: string; tool_records: unknown[] })[];
  clinical_impact: (Investigation['clinical_impact'][number] & { exposed_sessions: number; first_unmet_on: string | null })[];
};
export type WorkspaceForecast = ForecastSeries & { forecast: ForecastSeries['forecast'] & {
  as_of: string; end_on: string; product_identity: { presentation: string; total_mg: number; quantity_unit: 'vial' };
  initial_inventory_inputs: { on_hand: number; reserved: number; product_id: string; quality_status: string }[];
} };
export interface WorkspaceData { snapshot: WorkspaceCase; forecasts: WorkspaceForecast[]; actions: ActionDraft[] }

export function facilityForecast(data: WorkspaceData, id: string, label: ForecastSeries['case']) {
  return data.forecasts.find(f => f.case === label && f.forecast.request.hospital_id === id &&
    f.forecast.request.product_id === data.snapshot.mitigation.request.product_id)?.forecast;
}
export function recommended(data: WorkspaceData) {
  return data.snapshot.mitigation.evaluations.find(e => e.candidate.candidate_id === data.snapshot.mitigation.recommendation_id && e.feasible);
}
export function affected(data: WorkspaceData) {
  return data.snapshot.hospitals.filter(h => (facilityForecast(data, h.hospital_id, 'disruption')?.total_unmet ?? 0) > 0);
}
export function labelMedicine(data: WorkspaceData) {
  const product = data.snapshot.medicines.find(m => m.product_id === data.snapshot.mitigation.request.product_id);
  return product?.presentation.split(' unopened')[0] ?? data.snapshot.mitigation.request.product_id;
}
/** Bounds are curated scenario areas, so their centers are illustrative focus points. */
export function disruptionPoint(data: WorkspaceData): [number, number] | null {
  const areas = data.snapshot.intelligence?.signal.geography;
  if (!areas?.length) return null;
  const b = areas.reduce((a, b) => (b.east-b.west)*(b.north-b.south) < (a.east-a.west)*(a.north-a.south) ? b : a);
  return [(b.west+b.east)/2, (b.south+b.north)/2];
}
/** Fictional markers distributed inside Illinois for visual separation; never facility addresses. */
export const illustrativeIllinois: Record<string, [number, number]> = {
  'sim-central': [-89.4, 40.1], 'sim-north': [-88.7, 42.0], 'sim-south': [-89.2, 37.8],
};
