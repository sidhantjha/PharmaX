"""Simulation-only shipment reconciliation and immutable operator revisions."""
from datetime import date, datetime, timezone
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .models import Contract, SourceEvidence
from .risk import HospitalRiskAssessment


class ArrivalClaim(Contract):
    claim_id: str = Field(min_length=1, max_length=160)
    shipment_id: str = Field(min_length=1, max_length=160)
    product_id: str = Field(min_length=1, max_length=160)
    supplier_id: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=160)
    effective_at: AwareDatetime
    retrieved_at: AwareDatetime
    arrival_on: date
    document: str = Field(min_length=2, max_length=4000)
    authorization_ref: str | None = Field(default=None, max_length=160)
    evidence: SourceEvidence = Field(json_schema_extra={'allOf':[{'properties':{'kind':{'const':'simulated'}}}]})
    classification: Literal['simulated-operational-communication'] = 'simulated-operational-communication'

    @model_validator(mode='after')
    def simulation_only(self):
        if self.evidence.kind != 'simulated':
            raise ValueError('Operational communications must be explicitly simulated')
        return self


class ReconciliationRequest(Contract):
    fixture_version: Literal['october-arrival-conflict-v1'] = 'october-arrival-conflict-v1'
    decision_at: AwareDatetime = datetime(2026, 10, 24, 20, tzinfo=timezone.utc)
    records: list[ArrivalClaim] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def unique(self):
        if len({r.claim_id for r in self.records}) != len(self.records):
            raise ValueError('Duplicate claim ID')
        return self


class ClaimCheck(Contract):
    claim_id: str
    check: str
    passed: bool
    reason: str


class ClaimInspection(Contract):
    role: Literal['verification', 'disruption', 'dependency']
    status: Literal['complete', 'unavailable', 'failed']
    checks: list[ClaimCheck] = Field(default_factory=list)
    # Same bounded execution protocol as Phase 5B's original task results.
    findings: list[dict] = Field(default_factory=list, max_length=0)
    calculations: list[dict] = Field(default_factory=list, max_length=0)
    error_code: str | None = None


class EvidenceConflict(Contract):
    conflict_id: str
    competing_claims: list[ArrivalClaim]
    evidence_ids: list[str]
    reason: str
    assessments: list[ClaimCheck]
    verification_status: Literal['resolved-for-planning', 'unresolved', 'no-disagreement']
    selected_claim_id: str | None
    justification: str
    unresolved_uncertainty: list[str]


class ForecastAlternative(Contract):
    delay_days: int
    arrival_on: date
    claim_ids: list[str]
    adoption: Literal['selected-assumption', 'retained-assumption', 'conditional-only']
    assessment: HospitalRiskAssessment
    classification: Literal['calculated-from-simulated-assumption'] = 'calculated-from-simulated-assumption'


class ConflictResolution(Contract):
    resolution_id: str
    decision_at: AwareDatetime
    policy_version: str
    outcome: Literal['revised', 'unchanged', 'unresolved']
    prior_delay_days: int
    selected_delay_days: int | None
    selected_claim_id: str | None
    claims: list[ArrivalClaim]
    inspections: list[ClaimInspection]
    conflicts: list[EvidenceConflict]
    alternatives: list[ForecastAlternative] = Field(default_factory=list)
    advisory_evidence_ids: list[str]
    uncertainty: list[str]
    summary: str


class ForecastDelta(Contract):
    hospital_id: str
    previous_analysis_id: str
    revised_analysis_id: str
    previous_unmet_vials: int
    revised_unmet_vials: int
    unmet_delta: int
    previous_first_unmet: date | None
    revised_first_unmet: date | None
    previous_mitigated_unmet: int | None
    revised_mitigated_unmet: int | None


class OptionRecheck(Contract):
    evaluation_id: str
    candidate_id: str
    feasible: bool
    original_unmet: int
    remaining_unmet: int
    rejection_codes: list[str]
    evidence_ids: list[str]
    analysis_ids: list[str]


class CaseRevision(Contract):
    parent_id: str
    parent_snapshot_sha256: str
    resolution_id: str
    previous_scenario_version: str
    revised_scenario_version: str
    forecast_changed: bool
    disposition: Literal['maintained', 'revised', 'unresolved']
    previous_recommendation_id: str | None
    revised_recommendation_id: str | None
    previous_option_recheck: OptionRecheck | None
    forecast_deltas: list[ForecastDelta]
    prior_constraint_memory_refs: list[str]
    decision_summary: str
    classification: Literal['conditional-reprojection-with-new-simulated-knowledge'] = 'conditional-reprojection-with-new-simulated-knowledge'
