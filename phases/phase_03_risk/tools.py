"""Bounded, read-only Phase 4 analytical entry points over a validated cached dataset."""

from datetime import date
from contracts import ForecastRequest, ScenarioOverlay
from phases.phase_02_data.validation import validate_bundle
from .forecast import forecast, assess_hospital, compare
from .shipments import assess_shipment
from .trade import exposure


class RiskTools:
    def __init__(self, bundle, max_calls=20):
        self.bundle = validate_bundle(bundle).model_copy(deep=True)
        if type(max_calls) is not int or not 1 <= max_calls <= 20:
            raise ValueError("Analytical call budget must be 1..20")
        if sum(len(getattr(self.bundle, c)) for c in ("inventory", "consumption", "shipments")) > 500:
            raise ValueError("Pilot analytical input budget exceeded")
        self.max_calls = max_calls
        self.calls = 0

    def reserve(self):
        if self.calls >= self.max_calls:
            raise ValueError("Analytical call budget exhausted")
        self.calls += 1

    def trade_exposure(self, context_as_of):
        self.reserve()
        cutoff = date.fromisoformat(context_as_of) if isinstance(context_as_of, str) else context_as_of
        if not isinstance(cutoff, date):
            raise ValueError("Context cutoff must be a date")
        return exposure(self.bundle, context_as_of=cutoff)

    def inventory_forecast(self, request):
        self.reserve()
        return forecast(self.bundle, ForecastRequest.model_validate(request))

    def hospital_risk(self, request):
        self.reserve()
        return assess_hospital(self.bundle, ForecastRequest.model_validate(request))

    def scenario_comparison(self, request):
        self.reserve()
        return compare(self.bundle, ForecastRequest.model_validate(request))

    def shipment_risk(self, shipment_id, scenario="disruption", overlay=None):
        self.reserve()
        shipment = next((s for s in self.bundle.shipments if s.shipment_id == shipment_id), None)
        if shipment is None:
            raise ValueError("Unknown shipment")
        return assess_shipment(
            self.bundle,
            shipment,
            scenario,
            ScenarioOverlay.model_validate(overlay) if overlay is not None else None,
        )
