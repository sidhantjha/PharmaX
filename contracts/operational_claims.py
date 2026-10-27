"""Phase 7 claim semantics, independent origins and auditable reconciliation."""
from typing import Annotated, Literal
from pydantic import AwareDatetime, Field, model_validator
from .models import Contract, SourceEvidence

Meaning = Literal['production_status', 'allocation_policy', 'requested', 'acknowledged',
                  'shipped', 'delivered', 'accepted_receipt', 'usable_inventory',
                  'forecast_incoming', 'agency_guidance']


class OperationalClaim(Contract):
    claim_id: str = Field(min_length=1, max_length=160)
    source_id: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    originating_claim_id: str = Field(min_length=1)
    observation: Literal['independent', 'repeated', 'derived']
    meaning: Meaning
    classification: Literal['real', 'simulated', 'projected', 'calculated']
    product_id: str | None = None
    ndc: str | None = None
    local_sku: str | None = None
    facility_id: str | None = None
    account_id: str | None = None
    order_id: str | None = None
    line_id: str | None = None
    shipment_id: str | None = None
    quantity: int | None = Field(default=None, ge=0, strict=True)
    unit: Literal['bag', 'percent', 'case', 'status']
    case_bags: int | None = Field(default=None, gt=0, strict=True)
    status: str = Field(min_length=1)
    scope: Literal['policy', 'event_total', 'partial', 'snapshot', 'expectation']
    issued_at: AwareDatetime
    effective_at: AwareDatetime
    available_at: AwareDatetime
    expected_by: AwareDatetime | None = None
    verification: Literal['verified', 'unverified', 'rejected']
    supersedes: str | None = None
    evidence: SourceEvidence
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def semantics(self):
        if self.classification == 'simulated' and self.evidence.kind != 'simulated':
            raise ValueError('Synthetic claim must retain simulated provenance')
        if self.classification == 'real' and self.evidence.kind != 'observed':
            raise ValueError('Real claim requires observed document provenance')
        if self.unit == 'case' and not self.case_bags:
            raise ValueError('Explicit case conversion required')
        if self.unit == 'percent' and (self.meaning != 'allocation_policy' or
                                     self.quantity is None or self.quantity > 100):
            raise ValueError('Percentage is an allocation policy, not supply')
        if self.meaning in {'requested', 'acknowledged', 'shipped', 'delivered', 'accepted_receipt'}:
            if not all([self.product_id, self.ndc, self.local_sku, self.facility_id,
                        self.account_id, self.order_id, self.line_id]):
                raise ValueError('Operational event requires complete identity')
            if self.unit not in {'bag', 'case'} or self.quantity is None:
                raise ValueError('Operational quantity requires explicit container unit')
        if self.meaning in {'shipped', 'delivered', 'accepted_receipt'} and not self.shipment_id:
            raise ValueError('Fulfillment requires shipment identity')
        if self.issued_at > self.available_at:
            raise ValueError('Claim cannot be available before issue')
        if self.supersedes == self.claim_id:
            raise ValueError('Self supersession')
        return self


class CorroborationFinding(Contract):
    finding_id: str
    category: Literal['consistent', 'compatible', 'conflict', 'missing', 'superseded']
    claim_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    reason: str
    verification_steps: list[str]
    independent_origins: int = Field(ge=0)
    discrepancy_bags: int = Field(default=0, ge=0)
    requires_human_review: bool = False


class EvidenceVerification(Contract):
    schema_version: Literal['7.1'] = '7.1'
    scenario: str
    cutoff: AwareDatetime
    claims: list[OperationalClaim] = Field(max_length=200)
    findings: list[CorroborationFinding] = Field(max_length=1000)
    excluded_claim_ids: list[str]
    physical_receipts: dict[str, Annotated[int, Field(ge=0, strict=True)]]
    conditional_forecasts: list[dict]
    forecast_changes: list[dict]
    requires_human_review: bool
    limitations: list[str]

    @model_validator(mode='after')
    def references(self):
        claims = {c.claim_id: c for c in self.claims}
        if len(claims) != len(self.claims) or set(claims) & set(self.excluded_claim_ids):
            raise ValueError('Duplicate or admitted/excluded claim identity')
        for c in self.claims:
            if c.available_at > self.cutoff or c.issued_at > self.cutoff:
                raise ValueError('Future claim in admitted result')
        for f in self.findings:
            if any(cid not in claims for cid in f.claim_ids):
                raise ValueError('Finding references missing claim')
            if set(f.evidence_ids) != {claims[cid].evidence.evidence_id for cid in f.claim_ids}:
                raise ValueError('Finding evidence does not match referenced claims')
        if any(f.requires_human_review for f in self.findings) and not self.requires_human_review:
            raise ValueError('Material finding must retain human review')
        for shipment in self.physical_receipts:
            if not any(c.meaning == 'accepted_receipt' and c.shipment_id == shipment and
                       c.verification == 'verified' and c.observation == 'independent' for c in self.claims):
                raise ValueError('Receipt credit requires independent validated receiving record')
        return self
