"""Phase 4C additive news-to-decision boundary. Observed evidence ≠ simulated impact."""
from datetime import date
from typing import Literal
from pydantic import AwareDatetime, Field, HttpUrl, model_validator
from .models import Contract, SourceEvidence
from .data import Identifier
from .risk import HospitalRiskAssessment
from .mitigation import MitigationResult


class GeoBox(Contract):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode='after')
    def ordered(self):
        if self.west > self.east or self.south > self.north:
            raise ValueError('Invalid bounding rectangle')
        return self


class DisruptionSignal(Contract):
    schema_version: Literal['4c.1'] = '4c.1'
    event_id: Identifier
    revision_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    deduplication_identity: str
    category: Literal['maritime_security', 'port_disruption', 'manufacturing', 'trade_restriction', 'shipping', 'unknown']
    title: str = Field(min_length=1, max_length=1000)
    provider: Literal['marad', 'gdelt']
    source_type: Literal['official', 'media']
    publisher: str
    original_urls: list[HttpUrl] = Field(min_length=1, max_length=3)
    location: list[str] = Field(max_length=12)
    corridors: list[str] = Field(max_length=12)
    geography: list[GeoBox] = Field(default_factory=list, max_length=12)
    observed_event_time: AwareDatetime | None = None
    source_published_at: AwareDatetime | None = None
    source_published_on: date | None = None
    indexed_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime
    effective_on: date | None = None
    expires_on: date | None = None
    status: Literal['active', 'cancelled', 'unknown']
    supersedes: list[Identifier] = Field(default_factory=list, max_length=10)
    confirmation: Literal['official_notice', 'unverified_media']
    supported_claims: list[str] = Field(min_length=1, max_length=10)
    claim_limitations: list[str] = Field(min_length=1, max_length=20)
    impacted_assets: list[Identifier] = Field(default_factory=list, max_length=10)
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=5)
    evidence: list[SourceEvidence] = Field(min_length=1, max_length=5)
    evidence_label: Literal['observed'] = 'observed'

    @model_validator(mode='after')
    def lineage(self):
        if set(self.evidence_ids) != {e.evidence_id for e in self.evidence}:
            raise ValueError('Signal evidence IDs must resolve')
        if any(e.kind != 'observed' or e.observed_at > self.retrieved_at for e in self.evidence):
            raise ValueError('Invalid observed lineage')
        if self.effective_on and self.expires_on and self.effective_on > self.expires_on:
            raise ValueError('Reversed validity window')
        if ((self.provider == 'gdelt') != (self.confirmation == 'unverified_media')
                or (self.source_type == 'official') != (self.provider == 'marad')):
            raise ValueError('Source trust mismatch')
        return self


class SourceStatus(Contract):
    source: Literal['marad', 'gdelt']
    target_cadence_seconds: int
    last_attempt_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    next_check_at: AwareDatetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    new_revisions: int = 0
    raw_content_changed: bool = False
    content_needs_review: bool = False
    latest_receipt_ids: list[str] = Field(default_factory=list)
    monitoring: Literal['last-check-only'] = 'last-check-only'


class RouteLeg(Contract):
    leg_id: Identifier
    mode: Literal['sea', 'road']
    origin: str
    destination: str
    corridors: list[str]
    geography: GeoBox
    starts_on: date
    ends_on: date

    @model_validator(mode='after')
    def dates(self):
        if self.starts_on > self.ends_on:
            raise ValueError('Reversed route dates')
        return self


class SupplyDependency(Contract):
    scenario_id: Identifier
    dataset_version: str
    shipment_id: Identifier
    supplier_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    quantity_vials: int = Field(gt=0)
    planned_arrival: date
    legs: list[RouteLeg] = Field(min_length=1, max_length=12)
    assumptions: list[str]
    evidence_label: Literal['simulated'] = 'simulated'


class DependencyMatch(Contract):
    signal_revision: str
    shipment_id: Identifier
    status: Literal['MATCHED', 'POSSIBLE', 'UNMATCHED']
    reasons: list[str]
    matched_legs: list[Identifier]
    evidence_ids: list[Identifier]
    dependency_label: Literal['simulated'] = 'simulated'
    observed_shipment_delay: Literal[False] = False


class DelayRisk(Contract):
    delay_days: int = Field(ge=0, le=21, strict=True)
    assumed_arrival: date
    delivery_difference_days: int
    assessment: HospitalRiskAssessment
    assumption_label: Literal['simulated'] = 'simulated'
    result_label: Literal['calculated'] = 'calculated'


class IntelligenceRun(Contract):
    schema_version: Literal['4c.1'] = '4c.1'
    run_id: str
    mode: Literal['live-fetch', 'frozen-replay']
    decision_at: AwareDatetime
    signal: DisruptionSignal
    dependency: SupplyDependency
    match: DependencyMatch
    baseline: HospitalRiskAssessment | None = None
    sensitivity: list[DelayRisk] = Field(default_factory=list)
    selected_delay_days: int | None = None
    mitigation: MitigationResult | None = None
    events: list[dict] = Field(default_factory=list)
    uncertainties: list[str]
    zetaris: dict = Field(default_factory=dict)
    status: Literal['complete', 'unmatched', 'blocked']
    error_code: str | None = None
    historical: Literal[False] = False
    requires_human_review: Literal[True] = True
