"""Phase 2 versioned records; physical inventory quantities use whole unopened vials."""

from datetime import date
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, model_validator
from .models import Contract, SourceEvidence, TradeFlow, DrugShortage

Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
QuantityUnit = Literal["vial", "bag"]


class DrugProduct(Contract):
    model_config = ConfigDict(revalidate_instances="always")
    product_id: Identifier
    name: str
    package_ndc: str | None = Field(default=None, pattern=r"^\d{4,5}-\d{3,4}-\d{1,2}$")
    presentation: str
    strength_mg_per_ml: float | None = Field(default=None, gt=0)
    container_ml: float | None = Field(default=None, gt=0)
    total_mg: float | None = Field(default=None, gt=0)
    quantity_unit: Literal["vial"] = "vial"
    storage_min_c: float | None = None
    storage_max_c: float | None = None
    protect_from_light: bool | None = None
    evidence: SourceEvidence

    @model_validator(mode="after")
    def consistency(self):
        if (self.storage_min_c is None) != (self.storage_max_c is None):
            raise ValueError("Storage bounds must both be present or unknown")
        if self.storage_min_c is not None and self.storage_min_c > self.storage_max_c:
            raise ValueError("Invalid storage range")
        values = (self.strength_mg_per_ml, self.container_ml, self.total_mg)
        if any(v is not None for v in values) and not all(v is not None for v in values):
            raise ValueError("Strength, container and mass must be specified together")
        if all(v is not None for v in values) and abs(values[0] * values[1] - values[2]) > 1e-6:
            raise ValueError("Mass does not match strength and volume")
        return self


class DisruptionEvent(Contract):
    event_id: Identifier
    title: str
    event_type: Literal["manufacturing", "trade", "logistics", "regulatory"]
    location: str
    country_iso: str = Field(pattern=r"^[A-Z]{3}$")
    starts_on: date
    ends_on: date | None = None
    products_stated: list[str] = Field(default_factory=list)
    established: str
    uncertain_implications: str
    evidence: SourceEvidence

    @model_validator(mode="after")
    def dates(self):
        if self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("Event end precedes start")
        return self


class Supplier(Contract):
    supplier_id: Identifier
    name: str
    country_iso: str = Field(pattern=r"^[A-Z]{3}$")
    role: Literal["modeled_manufacturer", "modeled_distributor"]
    evidence: SourceEvidence


class Hospital(Contract):
    model_config = ConfigDict(revalidate_instances="always")
    hospital_id: Identifier
    name: str
    state: Literal["IL"] = "IL"
    country_iso: Literal["USA"] = "USA"
    storage_min_c: float = 20
    storage_max_c: float = 25
    protects_from_light: bool = True
    evidence: SourceEvidence


class SupplierApproval(Contract):
    approval_id: Identifier
    hospital_id: Identifier
    supplier_id: Identifier
    product_id: Identifier
    approved: bool
    evidence: SourceEvidence


class InventoryRecord(Contract):
    model_config = ConfigDict(revalidate_instances="always")
    inventory_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    supplier_id: Identifier
    lot_id: str
    as_of: date
    on_hand: int = Field(ge=0, strict=True)
    reserved: int = Field(ge=0, strict=True)
    expiry_date: date
    quality_status: Literal["released", "quarantined"]
    quantity_unit: Literal["vial"] = "vial"
    evidence: SourceEvidence

    @model_validator(mode="after")
    def quantity_dates(self):
        if self.reserved > self.on_hand or self.expiry_date < self.as_of:
            raise ValueError("Invalid reserved quantity or already-expired starting stock")
        return self


class DailyConsumption(Contract):
    model_config = ConfigDict(revalidate_instances="always")
    consumption_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    day: date
    quantity: int = Field(ge=0, strict=True)
    quantity_unit: Literal["vial"] = "vial"
    evidence: SourceEvidence


class PurchaseOrder(Contract):
    model_config = ConfigDict(revalidate_instances="always")
    purchase_order_id: Identifier
    hospital_id: Identifier
    supplier_id: Identifier
    product_id: Identifier
    quantity: int = Field(gt=0, strict=True)
    quantity_unit: Literal["vial"] = "vial"
    ordered_on: date
    expected_delivery: date
    evidence: SourceEvidence

    @model_validator(mode="after")
    def dates(self):
        if self.expected_delivery < self.ordered_on:
            raise ValueError("Delivery precedes order")
        return self


class Shipment(Contract):
    model_config = ConfigDict(revalidate_instances="always")
    shipment_id: Identifier
    purchase_order_id: Identifier
    supplier_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    quantity: int = Field(gt=0, strict=True)
    quantity_unit: Literal["vial"] = "vial"
    dispatched_on: date
    expected_arrival: date
    status: Literal["planned", "in_transit", "held"]
    origin_country_iso: str = Field(pattern=r"^[A-Z]{3}$")
    temperature_min_c: float
    temperature_max_c: float
    protects_from_light: bool
    disruption_event_ids: list[str] = Field(default_factory=list)
    evidence: SourceEvidence

    @model_validator(mode="after")
    def dates(self):
        if self.expected_arrival < self.dispatched_on or self.temperature_min_c > self.temperature_max_c:
            raise ValueError("Invalid shipment dates or temperature range")
        return self


class FDARecall(Contract):
    recall_id: str
    product_description: str
    recall_number: str
    classification: str | None = None
    status: str | None = None
    reason: str | None = None
    initiated_on: date | None = None
    reported_on: date | None = None
    evidence: SourceEvidence


class DataBundle(Contract):
    schema_version: Literal["2.0"] = "2.0"
    dataset_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["observed+simulated", "fixture"]
    scenario_as_of: date
    snapshot_ids: list[str]
    coverage: list[str]
    trade_flows: list[TradeFlow]
    products: list[DrugProduct]
    shortages: list[DrugShortage]
    events: list[DisruptionEvent]
    recalls: list[FDARecall]
    suppliers: list[Supplier]
    hospitals: list[Hospital]
    approvals: list[SupplierApproval]
    inventory: list[InventoryRecord]
    consumption: list[DailyConsumption]
    purchase_orders: list[PurchaseOrder]
    shipments: list[Shipment]
    evidence: list[SourceEvidence]


class ProductAlias(Contract):
    """An explicit reviewed assertion; no automatic base-code/suffix equivalence."""

    scheme: Literal["baxter", "ndc"]
    original: str = Field(min_length=1)
    normalized: str = Field(min_length=1)
    review_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def ndc_segments(self):
        if self.scheme == "ndc":
            parts = self.original.split("-")
            lengths = tuple(map(len, parts))
            if lengths not in {(4, 4, 2), (5, 3, 2), (5, 4, 1), (5, 4, 2)} or not all(
                p.isascii() and p.isdigit() for p in parts
            ):
                raise ValueError("NDC requires an unambiguous segmented identifier")
            if self.normalized != "-".join(p.zfill(n) for p, n in zip(parts, (5, 4, 2))):
                raise ValueError("NDC alias must use segment-aware 5-4-2 padding")
        return self


class ReplayProduct(DrugProduct):
    quantity_unit: QuantityUnit
    strength_mg_per_ml: float = Field(gt=0)
    container_ml: float = Field(gt=0)
    total_mg: float = Field(gt=0)
    aliases: list[ProductAlias] = Field(min_length=1)

    @model_validator(mode="after")
    def identity(self):
        keys = [(a.scheme, a.original) for a in self.aliases]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate or contradictory product aliases")
        if self.package_ndc and not any(
            a.scheme == "ndc" and a.original == self.package_ndc for a in self.aliases
        ):
            raise ValueError("Package NDC requires a reviewed alias")
        return self


class ReplayHospital(Hospital):
    state: Literal["NC"]
    network_id: Literal["sim-nc-health-system"]
    hospital_id: Literal["sim-nc-central", "sim-nc-north", "sim-nc-south"]

    @model_validator(mode="after")
    def fictional(self):
        if self.evidence.kind != "simulated":
            raise ValueError("Replay hospital network must remain simulated")
        return self


class ReplayInventory(InventoryRecord):
    quantity_unit: QuantityUnit


class ReplayConsumption(DailyConsumption):
    quantity_unit: QuantityUnit


class ReplayPurchaseOrder(PurchaseOrder):
    quantity_unit: QuantityUnit
    account_id: Identifier
    order_line_id: Identifier
    customer_item_id: Identifier


class ReplayShipment(Shipment):
    quantity_unit: QuantityUnit
    account_id: Identifier
    order_line_id: Identifier
    revision: int = Field(ge=1, strict=True)


class ReplayPurchasing(Contract):
    purchasing_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    supplier_id: Identifier
    account_id: Identifier
    customer_item_id: Identifier
    purchased_on: date
    quantity: int = Field(ge=0, strict=True)
    returned_quantity: int = Field(ge=0, strict=True)
    quantity_unit: QuantityUnit
    evidence: SourceEvidence

    @model_validator(mode="after")
    def returns(self):
        if self.returned_quantity > self.quantity:
            raise ValueError("Returns exceed purchases")
        return self


class ReplayAcknowledgment(Contract):
    acknowledgment_id: Identifier
    purchase_order_id: Identifier
    order_line_id: Identifier
    account_id: Identifier
    hospital_id: Identifier
    supplier_id: Identifier
    product_id: Identifier
    revision: int = Field(ge=1, strict=True)
    acknowledged_on: date
    status: Literal["accepted", "backordered", "held", "cancelled"]
    quantity: int = Field(ge=0, strict=True)
    quantity_unit: QuantityUnit
    expected_delivery: date | None
    evidence: SourceEvidence


class DataQuery(Contract):
    collection: Literal[
        "trade_flows",
        "products",
        "shortages",
        "events",
        "recalls",
        "suppliers",
        "hospitals",
        "approvals",
        "inventory",
        "consumption",
        "purchase_orders",
        "shipments",
        "evidence",
    ]
    reporter_code: int | None = Field(default=None, ge=1, le=999, strict=True)
    partner_code: int | None = Field(default=None, ge=1, le=999, strict=True)
    product_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    hospital_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    record_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,160}$")
    limit: int = Field(default=20, ge=1, le=100, strict=True)


class DataPage(Contract):
    schema_version: Literal["2.0"] = "2.0"
    dataset_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["live-zetaris", "cached", "fixture"]
    query: DataQuery
    items: list[
        TradeFlow
        | DrugProduct
        | DrugShortage
        | DisruptionEvent
        | FDARecall
        | Supplier
        | Hospital
        | SupplierApproval
        | InventoryRecord
        | DailyConsumption
        | PurchaseOrder
        | Shipment
        | SourceEvidence
    ]
    evidence: list[SourceEvidence]
    returned_count: int = Field(ge=0)
    truncated: bool | None
