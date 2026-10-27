"""Phase 3 deterministic analytical contracts; no operational action authority."""

from datetime import date
from typing import Literal
from pydantic import Field, model_validator
from .models import Contract, TradeFlow
from .data import Identifier, InventoryRecord, DailyConsumption, QuantityUnit

RiskLevel = Literal["Low", "Moderate", "High", "Critical"]


class Quantity(Contract):
    """6a.1 container count; never substitute into legacy *_vials fields."""

    schema_version: Literal["6a.1"] = "6a.1"
    value: int = Field(ge=0, strict=True)
    unit: QuantityUnit


class RiskRating(Contract):
    dimension: Literal["country", "shipment", "supplier", "hospital"]
    level: RiskLevel | None
    rule_id: str
    explanation: str
    measurements: dict[str, float | str | None]
    evidence_ids: list[str]
    limitations: list[str]


class StockOverride(Contract):
    inventory_id: Identifier
    on_hand: int = Field(ge=0, strict=True)
    reserved: int | None = Field(default=None, ge=0, strict=True)


class ArrivalOverride(Contract):
    shipment_id: Identifier
    arrival_on: date | None = None
    hold: bool = False

    @model_validator(mode="after")
    def action(self):
        if self.hold == (self.arrival_on is not None):
            raise ValueError("Specify either a hold or one hypothetical arrival date")
        return self


class ScenarioOverlay(Contract):
    overlay_id: Identifier
    base_dataset_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    as_of: date
    description: str
    kind: Literal["simulated"] = "simulated"
    stock: list[StockOverride] = Field(default_factory=list, max_length=20)
    arrivals: list[ArrivalOverride] = Field(default_factory=list, max_length=20)


class ForecastRequest(Contract):
    hospital_id: Identifier
    product_id: Identifier = "sim-carboplatin-450"
    horizon_days: int = Field(default=14, ge=1, le=60, strict=True)
    scenario: Literal["baseline", "disruption"] = "disruption"
    overlay: ScenarioOverlay | None = None
    arrival_shelf_life_days: int = Field(default=30, ge=1, le=365, strict=True)


class RiskMeta(Contract):
    schema_version: Literal["3.0"] = "3.0"
    algorithm_version: Literal["pharmax-risk-v1"] = "pharmax-risk-v1"
    analysis_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["derived"] = "derived"
    input_evidence_ids: list[str]
    assumptions: list[str]
    limitations: list[str]


class CountryExposure(Contract):
    country_iso: str
    country_code: int
    import_value_usd: float
    observed_sample_share: float | None
    national_import_share: float | None
    historical_change_pct: float | None
    historical_change_reason: str
    documented_country_event_ids: list[str]
    country_rating: RiskRating


class TradeExposure(RiskMeta):
    context_as_of: date
    forecast_input: Literal[False] = False
    reporter_code: int
    period: str
    hs_code: str
    classification: str
    input_flows: list[TradeFlow]
    world_input: TradeFlow | None
    previous_inputs: list[TradeFlow]
    observed_import_value_usd: float
    world_import_value_usd: float | None
    value_coverage_fraction: float | None
    complete_partner_coverage: bool
    national_hhi: float | None
    countries: list[CountryExposure]


class ShipmentRisk(RiskMeta):
    as_of: date
    scenario: Literal["baseline", "disruption"]
    overlay_version: str | None
    shipment_id: Identifier
    supplier_id: Identifier
    hospital_id: Identifier
    product_id: Identifier
    quantity: int
    quantity_unit: Literal["vial"] = "vial"
    original_status: str
    scheduled_arrival: date
    modeled_arrival: date | None
    arrival_basis: Literal[
        "modeled_schedule", "hypothetical_override", "held", "blocked_constraints", "past_due_not_recredited"
    ]
    delay_days: int | None
    linked_simulated_event_ids: list[str]
    rating: RiskRating
    input_values: dict[str, str | int | float | bool | None]


class LotBalance(Contract):
    lot_id: str
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    expiry_date: date
    available: int = Field(ge=0)
    quarantined: bool


class DailyStockPoint(Contract):
    day: date
    opening_physical: int = Field(ge=0)
    expired_physical: int = Field(ge=0)
    opening_usable: int = Field(ge=0)
    received: int = Field(ge=0)
    demand: int = Field(ge=0)
    served: int = Field(ge=0)
    unmet: int = Field(ge=0)
    closing_usable: int = Field(ge=0)
    closing_physical: int = Field(ge=0)
    reserved: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    buffer_days: float | None
    received_shipment_ids: list[str]
    lots: list[LotBalance]


class InventoryForecast(RiskMeta):
    request: ForecastRequest
    as_of: date
    end_on: date
    historical_backtest: Literal[False] = False
    dependency_kind: Literal["simulated"] = "simulated"
    product_identity: dict[str, str | float]
    overlay_version: str | None
    initial_inventory_inputs: list[InventoryRecord]
    consumption_inputs: list[DailyConsumption]
    excluded_temporal_evidence_ids: list[str]
    expected_daily_demand: int
    initial_usable: int
    total_received: int
    total_served: int
    total_unmet: int
    shortage_days: int
    longest_shortage_run: int
    projected_stockout_date: date | None
    right_censored: bool
    ending_buffer_days: float | None
    points: list[DailyStockPoint]
    shipment_risks: list[ShipmentRisk]


class HospitalRiskAssessment(RiskMeta):
    hospital_id: Identifier
    product_id: Identifier
    forecast: InventoryForecast
    rating: RiskRating
    supplier_ratings: dict[str, RiskRating]


class ScenarioComparison(RiskMeta):
    as_of: date
    hospital_id: Identifier
    product_id: Identifier
    overlay: ScenarioOverlay | None
    overlay_version: str | None
    baseline: HospitalRiskAssessment
    disruption: HospitalRiskAssessment
    incremental_unmet: int
    incremental_shortage_days: int
    stockout_advance_days: int | None
    baseline_already_short: bool
    causal_explanation: str
