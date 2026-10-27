"""Small versioned contracts for provenance, approved reads and diagnostics."""
from datetime import date
from typing import Any, Literal
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SourceEvidence(Contract):
    evidence_id: str = Field(min_length=1)
    kind: Literal['observed', 'derived', 'simulated']
    source: str = Field(min_length=1)
    source_record_id: str | None = None
    source_url: HttpUrl
    observed_at: AwareDatetime
    published_on: date | None = None
    updated_on: date | None = None
    version_available_at: AwareDatetime | None = None
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    raw_ref: str | None = None
    parent_ids: list[str] = Field(default_factory=list)
    note: str = ''

    @model_validator(mode='after')
    def provenance(self):
        if self.kind == 'derived' and not self.parent_ids:
            raise ValueError('Derived evidence requires parent references')
        if self.version_available_at and self.version_available_at > self.observed_at:
            raise ValueError('Version availability cannot follow observation')
        return self


class DrugShortage(Contract):
    record_id: str
    generic_name: str | None = None
    package_ndc: str | None = None
    company: str | None = None
    status: str | None = None
    availability: str | None = None
    presentation: str | None = None
    initial_posting_date: date | None = None
    update_date: date | None = None
    shortage_reason: str | None = None
    related_information: str | None = None
    therapeutic_category: str | None = None
    product_id: str | None = None
    evidence: SourceEvidence


class TradeFlow(Contract):
    record_id: str
    hs_code: str = Field(pattern=r'^\d{2,6}$')
    classification: str
    period: str = Field(pattern=r'^\d{4}(\d{2})?$')
    reporter_code: int = Field(ge=0)
    partner_code: int = Field(ge=0)
    flow: Literal['M', 'X', 'RM', 'RX']
    value_usd: float | None = Field(default=None, ge=0)
    is_aggregate: bool | None = None
    is_reported: bool | None = None
    reporter_name: str | None = None
    reporter_iso: str | None = None
    partner_name: str | None = None
    partner_iso: str | None = None
    commodity_description: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    quantity_unit: str | None = None
    quantity_estimated: bool | None = None
    evidence: SourceEvidence


class ToolRequest(Contract):
    request_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.:-]+$')
    name: Literal['zetaris.connection_check', 'fda.sample', 'comtrade.sample']
    limit: int = Field(default=3, ge=1, le=5, strict=True)


class ToolResult(Contract):
    request_id: str
    status: Literal['PASS', 'FAIL', 'BLOCKED']
    mode: Literal['live', 'fixture', 'mock']
    evidence_ids: list[str] = Field(default_factory=list)
    record_count: int = Field(default=0, ge=0)
    error_code: str | None = None


class InvestigationEvent(Contract):
    event_id: str
    timestamp: AwareDatetime
    mode: Literal['live', 'fixture', 'mock']
    operation: str
    status: Literal['PASS', 'FAIL', 'BLOCKED']
    request_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    model_id: str | None = None
    tokens: dict[str, int] = Field(default_factory=dict)
    error_code: str | None = None


class CheckResult(Contract):
    name: str
    status: Literal['PASS', 'FAIL', 'BLOCKED']
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)
    record_count: int = Field(default=0, ge=0)


class DiagnosticReport(Contract):
    schema_version: Literal['1.0'] = '1.0'
    product: Literal['PharmaX'] = 'PharmaX'
    checked_at: AwareDatetime
    mode: Literal['live', 'fixture']
    checks: list[CheckResult]
    shortages: list[DrugShortage] = Field(default_factory=list)
    trade_flows: list[TradeFlow] = Field(default_factory=list)
    events: list[InvestigationEvent] = Field(default_factory=list)
    repository: dict[str, Any] = Field(default_factory=dict)
