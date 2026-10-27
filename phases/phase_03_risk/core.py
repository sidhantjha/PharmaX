"""Stable result identities, knowledge-cutoff checks and explicit rule thresholds."""

from datetime import datetime, time, timezone
from contracts import RiskRating
from phases.phase_01_foundation.sources import digest

LEVELS = ("Low", "Moderate", "High", "Critical")


def result(model, bundle, **values):
    value = model(dataset_version=bundle.dataset_version, analysis_id="0" * 64, **values)
    identity = digest(value.model_dump(mode="json", exclude={"analysis_id"}))
    return value.model_copy(update={"analysis_id": identity}, deep=True)


def available(evidence, cutoff, registry, visiting=None, *, day_start=False):
    """Publication never proves revised contents were available at a cutoff."""
    visiting = set() if visiting is None else set(visiting)
    if evidence.evidence_id in visiting:
        return False
    visiting.add(evidence.evidence_id)
    end = datetime.combine(cutoff, time.min if day_start else time.max, tzinfo=timezone.utc)
    known = evidence.version_available_at or evidence.observed_at
    if known > end or (evidence.updated_on and evidence.updated_on > cutoff):
        return False
    return all(
        parent in registry and available(registry[parent], cutoff, registry, visiting, day_start=day_start)
        for parent in evidence.parent_ids
    )


def require_synthetic(records, bundle):
    registry = {e.evidence_id: e for e in bundle.evidence}
    for record in records:
        if record.evidence.kind != "simulated" or not available(
            record.evidence, bundle.scenario_as_of, registry, day_start=True
        ):
            raise ValueError("Forecast dependency is not a cutoff-eligible simulated input")


def rating(dimension, level, rule, explanation, measurements, evidence_ids, limitations=None):
    return RiskRating(
        dimension=dimension,
        level=level,
        rule_id=rule,
        explanation=explanation,
        measurements=measurements,
        evidence_ids=sorted(set(evidence_ids)),
        limitations=limitations
        or ["Rule-based scenario review priority; not clinically or predictively calibrated."],
    )


def country_level(share):
    return "Low" if share < 0.10 else "Moderate" if share < 0.25 else "High" if share < 0.50 else "Critical"


def hospital_rating(forecast):
    first = forecast.projected_stockout_date
    buffers = [p.buffer_days for p in forecast.points if p.buffer_days is not None]
    if first is not None:
        offset = (first - forecast.as_of).days
        level = "Critical" if offset <= 1 else "High"
        rule = "hospital.unmet_within_2_days" if offset <= 1 else "hospital.unmet_later_in_horizon"
    elif buffers and min(buffers) < 3:
        level, rule = "Moderate", "hospital.buffer_below_3_days"
    else:
        level, rule = "Low", "hospital.no_unmet_and_buffer_at_least_3_days"
    return rating(
        "hospital",
        level,
        rule,
        f"{forecast.total_unmet} vials unmet over {forecast.shortage_days} days in the modeled horizon; "
        f"first unmet-demand date={first}.",
        {
            "unmet_vials": forecast.total_unmet,
            "shortage_days": forecast.shortage_days,
            "minimum_closing_buffer_days": min(buffers) if buffers else None,
        },
        forecast.input_evidence_ids,
    )
