"""Additive Phase 4B mitigation contracts; proposals never authorize physical actions."""
from datetime import date
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .models import Contract, SourceEvidence
from .data import Identifier
from .risk import RiskLevel, ScenarioComparison, ScenarioOverlay, HospitalRiskAssessment


class MitigationRequest(Contract):
    session_id: Identifier
    objective: str = Field(default='Investigate simulated alternatives to reduce Central shortage safely.',
                           min_length=3, max_length=500)
    scenario: Literal['original', 'clean-onset'] = 'original'
    evidence_as_of: AwareDatetime
    horizon_days: int = Field(default=14, ge=1, le=60, strict=True)
    hospital_id: Identifier = 'sim-central'
    product_id: Identifier = 'sim-carboplatin-450'
    reasoning: Literal['fixture', 'nightshift'] = 'fixture'


class SupplierStock(Contract):
    stock_id: Identifier
    supplier_id: Identifier
    product_id: Identifier
    presentation: str
    on_hand: int = Field(ge=0, strict=True)
    reserved: int = Field(ge=0, strict=True)
    quality_status: Literal['released', 'quarantined']
    expiry_date: date
    available_on: date
    evidence: SourceEvidence

    @model_validator(mode='after')
    def reservations(self):
        if self.reserved > self.on_hand:
            raise ValueError('Reservations exceed stock')
        return self


class SupplyRoute(Contract):
    route_id: Identifier
    source_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    authorized: bool
    transfer_permitted: bool = False
    max_quantity: int = Field(ge=1, le=10000, strict=True)
    earliest_dispatch: date
    transit_days: int = Field(ge=0, le=30, strict=True)
    temperature_min_c: float
    temperature_max_c: float
    protects_from_light: bool
    release_hold_authorized: bool = False
    evidence: SourceEvidence

    @model_validator(mode='after')
    def temperatures(self):
        if self.temperature_min_c > self.temperature_max_c:
            raise ValueError('Reversed transport temperatures')
        return self


class MitigationCandidate(Contract):
    candidate_id: Identifier
    kind: Literal['transfer', 'supplier_delivery', 'reschedule']
    source_id: Identifier
    route_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    presentation: str
    quantity: int = Field(gt=0, le=10000, strict=True)
    dispatch_on: date
    arrival_on: date
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=30)
    kind_label: Literal['simulated'] = 'simulated'


class MitigationCatalog(Contract):
    catalog_id: Identifier
    base_dataset_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    scenario_as_of: date
    available_at: AwareDatetime
    supplier_stocks: list[SupplierStock] = Field(max_length=20)
    routes: list[SupplyRoute] = Field(max_length=20)
    candidates: list[MitigationCandidate] = Field(max_length=12)
    evidence: list[SourceEvidence] = Field(max_length=60)
    assumptions: list[str] = Field(max_length=30)
    kind: Literal['simulated'] = 'simulated'


ReasonCode = Literal[
    'UNKNOWN_SOURCE', 'UNKNOWN_ROUTE', 'PRODUCT_MISMATCH', 'PRESENTATION_MISMATCH',
    'INSUFFICIENT_UNRESERVED_STOCK', 'QUARANTINED', 'EXPIRED', 'INSUFFICIENT_SHELF_LIFE',
    'SOURCE_NOT_AVAILABLE', 'SOURCE_AUTHORIZATION', 'TRANSFER_NOT_AUTHORIZED',
    'ROUTE_CAPACITY', 'DELIVERY_TOO_LATE', 'INVALID_DELIVERY_DATE', 'TRANSPORT_REQUIREMENTS',
    'STORAGE_REQUIREMENTS', 'HOLD_NOT_RELEASED', 'DONOR_SHORTAGE', 'DONOR_RISK_WORSE',
    'EVIDENCE_AFTER_CUTOFF', 'SCENARIO_DATE_MISMATCH', 'FUTURE_TRANSFER_UNSUPPORTED',
    'NO_IMPROVEMENT', 'TARGET_MISMATCH', 'EVIDENCE_LINEAGE',
]


class ConstraintFailure(Contract):
    code: ReasonCode
    detail: str
    evidence_ids: list[Identifier]


class HospitalMetrics(Contract):
    hospital_id: Identifier
    unmet_vials: int = Field(ge=0)
    shortage_onset: date | None
    shortage_days: int = Field(ge=0)
    risk: RiskLevel
    ending_usable: int = Field(ge=0)


class MitigationImpact(Contract):
    hospital_id: Identifier
    before: HospitalMetrics
    projected: HospitalRiskAssessment | None = None
    # Projections on a rejected plan are diagnostics, never accepted actions.
    effective: HospitalMetrics
    donor: bool = False


class SupplyBalance(Contract):
    source_id: Identifier
    available_before: int = Field(ge=0)
    available_after: int = Field(ge=0)
    debited: int = Field(ge=0)
    recipient_receipt: int = Field(ge=0)
    existing_shipment_reused: bool
    conserved: bool
    diagnostic_only: bool = False


class CandidateEvaluation(Contract):
    evaluation_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    candidate: MitigationCandidate
    feasible: bool
    failures: list[ConstraintFailure]
    original_unmet: int = Field(ge=0)
    remaining_unmet: int = Field(ge=0)
    quantity_moved: int = Field(ge=0)
    impacts: list[MitigationImpact]
    supply_balance: SupplyBalance | None = None
    projected_dataset_version: str | None = None
    evidence_ids: list[Identifier]
    analysis_ids: list[str]
    projection_evidence: list[SourceEvidence] = Field(default_factory=list)
    simulated: Literal[True] = True
    requires_human_review: Literal[True] = True


class MitigationResult(Contract):
    schema_version: Literal['4b.1'] = '4b.1'
    request: MitigationRequest
    completed_at: AwareDatetime | None = None
    base_dataset_version: str
    scenario_dataset_version: str | None = None
    catalog_version: str
    overlay: ScenarioOverlay | None = None
    overlay_version: str | None = None
    status: Literal['complete', 'incomplete', 'blocked']
    stop_reason: Literal['exhausted', 'no_shortage', 'candidate_budget', 'tool_budget',
                         'model_stop', 'timeout', 'blocked']
    reference: list[ScenarioComparison] = Field(default_factory=list)
    evaluations: list[CandidateEvaluation] = Field(default_factory=list)
    recommendation_id: Identifier | None = None
    recommendation: str | None = None
    evidence: list[SourceEvidence] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    error_code: str | None = None
    requires_human_review: Literal[True] = True
    simulated: Literal[True] = True
    historical_backtest: Literal[False] = False
