"""Phase-local versioned task/trace boundary; no change to earlier producers."""
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from contracts.models import Contract, SourceEvidence
from contracts.data import Identifier

Role = Literal['trade', 'disruption', 'hospital']


class InvestigationRequest(Contract):
    session_id: Identifier
    objective: str = Field(min_length=3, max_length=500)
    evidence_as_of: AwareDatetime
    mode: Literal['cached', 'fixture', 'live-zetaris'] = 'cached'
    historical: bool = False
    horizon_days: int = Field(default=14, ge=1, le=60, strict=True)


class Plan(Contract):
    tasks: list[Role] = Field(min_length=1, max_length=3)
    decision: Literal['supply_consequences', 'trade_context', 'source_check']

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError('Duplicate specialist')
        return self


class Review(Contract):
    followup: Literal['none', 'disruption_link', 'trade_coverage']
    reason: Literal['sufficient', 'missing_link', 'missing_denominator', 'conflict', 'task_failure']


class Finding(Contract):
    claim_key: Identifier
    # The key includes scenario/horizon identity when the claim is numerical.
    value: str = Field(min_length=1, max_length=4000)
    kind: Literal['observed', 'derived', 'simulated']
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)
    source_ids: list[str] = Field(min_length=1, max_length=100)
    analysis_ids: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class TaskResult(Contract):
    role: Role
    status: Literal['complete', 'failed', 'unavailable']
    findings: list[Finding] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    error_code: str | None = None
    calculations: list[dict] = Field(default_factory=list)


class TraceEvent(Contract):
    sequence: int
    operation: str
    status: Literal['started', 'complete', 'failed', 'blocked']
    data: dict


class InvestigationResult(Contract):
    schema_version: Literal['4a.1'] = '4a.1'
    request: InvestigationRequest
    dataset_version: str
    status: Literal['complete', 'incomplete', 'blocked']
    plan: Plan | None = None
    review: Review | None = None
    tasks: list[TaskResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    memory: list[dict] = Field(default_factory=list)
    events: list[TraceEvent] = Field(default_factory=list)
    evidence: list[SourceEvidence] = Field(default_factory=list)
    historical_backtest: Literal[False] = False
    error_code: str | None = None
