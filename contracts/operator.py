"""Additive Phase 5 operator boundary; no dispatch or clinical authority."""
from datetime import date
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .models import Contract, SourceEvidence
from .data import Identifier, DrugProduct, Hospital
from .intelligence import IntelligenceRun, DisruptionSignal, SourceStatus
from .mitigation import MitigationResult, CandidateEvaluation
from .risk import InventoryForecast
from .reconciliation import ConflictResolution, CaseRevision, EvidenceConflict, ForecastDelta


class VerificationCheck(Contract):
    check: Literal['identity', 'document_support', 'dates', 'corroboration', 'contradictions', 'operational_relevance']
    status: Literal['supported', 'limited', 'unsupported', 'unavailable']
    explanation: str
    evidence_ids: list[str]


class AgentDecision(Contract):
    agent_id: Identifier
    responsibility: str
    task: str
    status: Literal['complete', 'unavailable', 'blocked']
    started_at: AwareDatetime
    completed_at: AwareDatetime
    execution: Literal['local-deterministic', 'nightshift'] = 'local-deterministic'
    tool_records: list[dict]
    evidence_ids: list[str]
    explanation: str
    limitations: list[str]
    rejected: list[str] = Field(default_factory=list)
    next_action: str
    memory_ids: list[str] = Field(default_factory=list)


class ClinicalSession(Contract):
    schedule_version: Literal['synthetic-schedule-v1'] = 'synthetic-schedule-v1'
    session_id: Identifier
    patient_id: Identifier
    hospital_id: Identifier
    clinical_service: str = 'oncology infusion / oncology pharmacy preparation'
    scheduled_on: date
    product_id: Identifier
    required_quantity: int = Field(gt=0)
    quantity_unit: Literal['vial'] = 'vial'
    allocation_constraint: str = 'Exact product; stable session-ID order; partial supply leaves session exposed; no regimen changes.'
    scenario_id: str
    dataset_version: str
    source_evidence_ids: list[str]
    classification: Literal['synthetic'] = 'synthetic'


class ClinicalAllocation(Contract):
    session_id: str
    allocated: int = Field(ge=0)
    uncovered: int = Field(ge=0)


class ClinicalImpact(Contract):
    schedule_version: Literal['synthetic-schedule-v1'] = 'synthetic-schedule-v1'
    case: Literal['baseline', 'disruption', 'mitigation']
    hospital_id: str
    unique_exposed_patients: int
    exposed_sessions: int
    first_unmet_on: date | None
    services: list[str]
    allocations: list[ClinicalAllocation]
    analysis_id: str
    classification: Literal['calculated-from-synthetic-schedules'] = 'calculated-from-synthetic-schedules'
    limitations: list[str]


ActionState = Literal['DRAFT', 'READY_FOR_REVIEW', 'APPROVED', 'SENT', 'SUBMITTED', 'CONFIRMED', 'COMPLETED']
ActionKind = Literal['supplier_inquiry', 'purchase_order', 'stock_transfer', 'procurement_approval',
                     'clinical_review', 'follow_up']


class ActionPackage(Contract):
    schema_version: Literal['5a.1'] = '5a.1'
    action_id: Identifier
    investigation_id: Identifier
    mitigation_id: str
    kind: ActionKind
    product_id: str
    quantity: int = Field(gt=0)
    quantity_unit: Literal['vial'] = 'vial'
    requesting_organization: str
    receiving_organization: str
    source_organization: str
    source_record_id: str
    target_on: date
    justification: str
    expected_benefit: str
    evidence_ids: list[str]
    calculation_ids: list[str]
    evidence_url: str
    calculation_url: str
    required_authorizations: list[str]
    outstanding_verification: list[str]
    draft: str
    status: ActionState = 'DRAFT'
    revision: int = Field(default=1, ge=1)
    updated_at: AwareDatetime
    review_history: list[dict] = Field(default_factory=list)
    classification: Literal['simulated-plan-draft'] = 'simulated-plan-draft'
    dispatch_available: Literal[False] = False
    clinical_alternative_state: Literal['REQUIRES_CLINICAL_REVIEW'] = 'REQUIRES_CLINICAL_REVIEW'


class ActionReview(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    status: Literal['READY_FOR_REVIEW', 'APPROVED']
    operator: str = Field(min_length=2, max_length=100)
    note: str = Field(min_length=3, max_length=1000)
    attestations: list[str] = Field(default_factory=list, max_length=20)


class CaseRequest(Contract):
    scenario: Literal['red-sea', 'original', 'clean-onset'] = 'red-sea'
    delay_days: int = Field(default=7, ge=0, le=21, strict=True)
    parent_id: Identifier | None = None

    @model_validator(mode='after')
    def scope(self):
        if self.scenario != 'red-sea' and self.delay_days != 7:
            raise ValueError('Delay sensitivity is supported only for the October route scenario')
        return self


class ExecutionTask(Contract):
    task_id: str
    agent_id: str
    parent_id: str | None = None
    dependency_ids: list[str] = Field(default_factory=list)
    dependency_policy: Literal['success', 'settled'] = 'success'
    read_only: bool = False
    launched_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    status: Literal['launched', 'running', 'complete', 'failed', 'timed_out'] = 'launched'
    error_code: str | None = None


class DecisionRecord(Contract):
    record_id: str
    sequence: int
    task_id: str | None = None
    operation_id: str
    agent_id: str
    operation: str
    status: str
    executed_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    tool_identifier: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    summary: dict
    classification: Literal['executed-operation', 'agent-recommendation']


class OperatorCase(Contract):
    schema_version: Literal['5a.1'] = '5a.1'
    investigation_id: Identifier
    parent_id: str | None
    created_at: AwareDatetime
    scenario: Literal['red-sea', 'original', 'clean-onset']
    mode: Literal['offline-replay', 'live-source', 'retained-source-live-model']
    objective: str
    model_execution: Literal['deterministic-local', 'nightshift'] = 'deterministic-local'
    dataset_version: str
    status: Literal['complete', 'incomplete', 'blocked']
    intelligence: IntelligenceRun | None
    mitigation: MitigationResult
    verification: list[VerificationCheck]
    agents: list[AgentDecision]
    evidence: list[SourceEvidence]
    medicines: list[DrugProduct]
    hospitals: list[Hospital]
    schedules: list[ClinicalSession]
    clinical_impact: list[ClinicalImpact]
    actions: list[ActionPackage]
    source_status: list[SourceStatus]
    source_receipt: dict
    context_findings: list[dict]
    limitations: list[str]
    execution_graph: list[ExecutionTask] = Field(default_factory=list)
    decision_ledger: list[DecisionRecord] = Field(default_factory=list)
    conflict_resolution: ConflictResolution | None = None
    revision: CaseRevision | None = None
    report_version: Literal['operator-briefing-v1'] = 'operator-briefing-v1'


class CaseSummary(Contract):
    investigation_id: str
    scenario: str
    created_at: AwareDatetime
    status: str
    parent_id: str | None


class MapLayer(Contract):
    layer_id: str
    geometry_type: Literal['bounding-area'] = 'bounding-area'
    bounds: list[float] = Field(min_length=4, max_length=4)
    source_ids: list[str]
    label: str
    alert_state: Literal['conditional-exposure', 'advisory']
    classification: Literal['simulated-route', 'derived-source-geography']
    limitations: str


class Health(Contract):
    status: Literal['ok'] = 'ok'
    checked_at: AwareDatetime
    storage: str
    model_runtime: str
    zetaris: str
    network: str


class ForecastSeries(Contract):
    case: Literal['baseline', 'disruption', 'mitigation']
    forecast: InventoryForecast


class ReportExport(Contract):
    version: Literal['operator-briefing-v1'] = 'operator-briefing-v1'
    format: Literal['json', 'csv', 'markdown']
    content: OperatorCase | str


class SourceHealth(Contract):
    status: SourceStatus
    evaluated_at: AwareDatetime
    stale: bool
    evidence_mode: Literal['retained-status-snapshot'] = 'retained-status-snapshot'
    monitoring: Literal['not-running-in-operator-server'] = 'not-running-in-operator-server'


class ApiResponse(Contract):
    schema_version: Literal['5a.1'] = '5a.1'
    resource: str
    investigation_id: str | None = None
    as_of: AwareDatetime
    availability: Literal['available', 'unavailable'] = 'available'
    reason: str | None = None
    offset: int = 0
    limit: int = 20
    total: int = 0
    items: list[Health | CaseSummary | OperatorCase | DrugProduct | Hospital | MapLayer |
                AgentDecision | SourceEvidence | ClinicalImpact | ActionPackage | VerificationCheck |
                SourceHealth | DisruptionSignal | CandidateEvaluation | ForecastSeries | ReportExport |
                ExecutionTask | DecisionRecord | ConflictResolution | CaseRevision | EvidenceConflict | ForecastDelta]


class ApiError(Contract):
    schema_version: Literal['5a.1'] = '5a.1'
    error: str = Field(pattern=r'^[A-Z_]+$')


class ChatRequest(Contract):
    question: str = Field(min_length=3, max_length=1000)
    execution_mode: Literal['offline', 'live'] = 'offline'
    investigation_id: Identifier | None = None
    scenario: Literal['red-sea', 'original', 'clean-onset'] = 'red-sea'


class ChatResponse(Contract):
    schema_version: Literal['5a.1'] = '5a.1'
    investigation_id: str
    answer: str
    citations: list[SourceEvidence]
    classification: Literal['deterministic-tool-response', 'live-model-tool-response',
                            'model-unavailable', 'inference-not-authorized'] = 'deterministic-tool-response'
    execution_state: Literal['deterministic-offline-replay', 'live-model-tool-execution',
                             'model-unavailable', 'inference-not-authorized'] = 'deterministic-offline-replay'
    error_code: str | None = None
    decisions: list[dict]
    references: list[str]
    limitations: list[str]
