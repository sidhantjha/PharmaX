"""8.1 product resolution and explicit local simulation requests."""
from datetime import date
from typing import Literal
from pydantic import Field
from .models import Contract
from .data import Identifier


class ProductIdentity(Contract):
    schema_version: Literal['8.1'] = '8.1'
    source_organization: str = Field(min_length=1)
    local_sku: str = Field(min_length=1)
    distributor_item: str | None = None
    manufacturer_code: str | None = None
    ndc: str | None = None
    name: str = Field(min_length=1)
    strength_mg_ml: int = Field(gt=0, strict=True)
    volume_ml: int = Field(gt=0, strict=True)
    dosage_form: Literal['injection', 'irrigation']
    container: Literal['viaflex-bag', 'bottle']
    units_per_case: int = Field(gt=0, strict=True)
    manufacturer: str = Field(min_length=1)
    labeler: str = Field(min_length=1)
    gs1_gtin: str | None = None
    mapping_reference: str = Field(min_length=1)


class ActionProposal(Contract):
    schema_version: Literal['8.1'] = '8.1'
    kind: Literal['transfer', 'replenishment']
    recipient: Identifier
    donor: Identifier | None = None
    product_id: Identifier
    lot_id: str = Field(min_length=1)
    quantity: int = Field(gt=0, le=100000, strict=True)
    unit: Literal['bag', 'case'] = 'bag'
    units_per_case: int = Field(gt=0, strict=True)
    expected_on: date
    actor: str = Field(min_length=1, max_length=160)


class ActionTransition(Contract):
    schema_version: Literal['8.1'] = '8.1'
    expected_revision: int = Field(ge=1, strict=True)
    state: Literal['EVIDENCE_REVIEW', 'AUTHORIZATION_PENDING', 'AUTHORIZED', 'REQUEST_PREPARED',
                   'SIMULATED_DISPATCH', 'SIMULATED_RECEIPT', 'CLOSED', 'REJECTED',
                   'CANCELLED', 'EXPIRED', 'INFORMATION_REQUESTED']
    actor: str = Field(min_length=1, max_length=160)
    role: Literal['operator', 'pharmacist', 'receiving']
    attested: bool
    note: str = Field(min_length=1, max_length=2000)
    policy_reference: str | None = Field(default=None, min_length=1)
    receipt_id: Identifier | None = None
    received_quantity: int | None = Field(default=None, ge=0, le=100000, strict=True)
    lot_id: str | None = None
    expires_on: date | None = None
    quality: Literal['released', 'quarantined', 'recalled'] | None = None
