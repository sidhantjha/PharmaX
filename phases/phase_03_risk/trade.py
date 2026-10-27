"""Retrospective country exposure, without extrapolating a partial partner sample."""

from datetime import date
from calendar import monthrange
from decimal import Decimal
from urllib.parse import urlsplit, parse_qs
from contracts import TradeExposure
from contracts.risk import CountryExposure
from .core import available, country_level, rating, result


def period_end(period):
    if len(period) == 4:
        return date(int(period), 12, 31)
    if len(period) == 6:
        year, month = int(period[:4]), int(period[4:])
        return date(year, month, monthrange(year, month)[1])
    raise ValueError("Unsupported reporting period")


def adjacent(old, new):
    if len(old) != len(new):
        return False
    if len(old) == 4:
        return int(new) == int(old) + 1
    previous = period_end(old)
    return int(new[:4]) * 12 + int(new[4:]) == previous.year * 12 + previous.month + 1


def scope(row):
    params = parse_qs(urlsplit(str(row.evidence.source_url)).query)
    keys = ("partner2Code", "customsCode", "motCode")
    if any(k not in params or len(params[k]) != 1 for k in keys):
        return None
    return tuple(params[k][0] for k in keys)


def comparable(a, b):
    return (
        (a.reporter_code, a.hs_code, a.classification, a.flow)
        == (b.reporter_code, b.hs_code, b.classification, b.flow)
        and scope(a) is not None
        and scope(a) == scope(b)
    )


def exposure(bundle, *, context_as_of, world=None, previous=()):
    if len(previous) > 100:
        raise ValueError("Prior-period input budget exceeded")
    registry = {e.evidence_id: e for e in bundle.evidence}
    rows = list(bundle.trade_flows)
    if not rows or len(rows) > 100:
        raise ValueError("Trade analysis requires 1..100 rows")
    first = rows[0]
    if first.flow != "M":
        raise ValueError("Import exposure requires import flows")
    if any(r.period != first.period or not comparable(first, r) or r.partner_code == 0 for r in rows):
        raise ValueError("Trade sample scope is not comparable")
    if len({r.partner_code for r in rows}) != len(rows):
        raise ValueError("Duplicate partner rows")
    if any(
        r.value_usd is None
        or not available(r.evidence, context_as_of, registry)
        or period_end(r.period) > context_as_of
        for r in rows
    ):
        raise ValueError("Trade values or revisions unavailable at context cutoff")
    total = sum((Decimal(str(r.value_usd)) for r in rows), Decimal(0))
    valid_world = (
        world is not None
        and world.partner_code == 0
        and world.period == first.period
        and comparable(first, world)
        and world.value_usd is not None
        and world.value_usd > 0
        and period_end(world.period) <= context_as_of
        and available(world.evidence, context_as_of, {**registry, world.evidence.evidence_id: world.evidence})
    )
    denominator = Decimal(str(world.value_usd)) if valid_world else None
    if denominator is not None and total > denominator + Decimal("0.01"):
        raise ValueError("Partner total exceeds comparable world denominator")
    complete = denominator is not None and abs(total - denominator) <= Decimal("0.01")
    ids = [r.evidence.evidence_id for r in rows]
    if valid_world:
        ids.append(world.evidence.evidence_id)
    countries = []
    for row in sorted(rows, key=lambda r: r.partner_code):
        share = float(Decimal(str(row.value_usd)) / total) if total > 0 else None
        national = float(Decimal(str(row.value_usd)) / denominator) if denominator else None
        priors = [
            p
            for p in previous
            if p.partner_code == row.partner_code
            and comparable(row, p)
            and adjacent(p.period, row.period)
            and p.value_usd is not None
            and p.value_usd > 0
            and period_end(p.period) <= context_as_of
            and available(p.evidence, context_as_of, {**registry, p.evidence.evidence_id: p.evidence})
        ]
        change = (
            float((Decimal(str(row.value_usd)) / Decimal(str(priors[0].value_usd)) - 1) * 100)
            if len(priors) == 1
            else None
        )
        if len(priors) == 1:
            ids.append(priors[0].evidence.evidence_id)
        events = [
            e
            for e in bundle.events
            if e.country_iso == row.partner_iso
            and e.evidence.kind != "simulated"
            and e.starts_on <= context_as_of
            and available(e.evidence, context_as_of, registry)
        ]
        level = country_level(national) if complete else None
        review = rating(
            "country",
            level,
            "country.complete_share_threshold" if complete else "country.insufficient_coverage",
            (
                "Complete comparable partner universe supports the national-share review rule."
                if complete
                else "National country risk is unclassified: the partner sample is incomplete. Sample share is not national dependence."
            ),
            {"observed_sample_share": share, "national_import_share": national},
            [row.evidence.evidence_id],
            [
                "HS30 aggregates do not identify carboplatin suppliers or shipments. Geographic event matches do not prove product impacts."
            ],
        )
        countries.append(
            CountryExposure(
                country_iso=row.partner_iso or "unknown",
                country_code=row.partner_code,
                import_value_usd=row.value_usd,
                observed_sample_share=share,
                national_import_share=national,
                historical_change_pct=change,
                historical_change_reason="Adjacent, comparable, available periods."
                if change is not None
                else "No unique adjacent comparable prior-period value available.",
                documented_country_event_ids=[e.event_id for e in events],
                country_rating=review,
            )
        )
    return result(
        TradeExposure,
        bundle,
        context_as_of=context_as_of,
        reporter_code=first.reporter_code,
        period=first.period,
        hs_code=first.hs_code,
        classification=first.classification,
        input_flows=rows,
        world_input=world,
        previous_inputs=list(previous),
        observed_import_value_usd=float(total),
        world_import_value_usd=float(denominator) if denominator else None,
        value_coverage_fraction=float(total / denominator) if denominator else None,
        complete_partner_coverage=complete,
        national_hhi=sum((c.national_import_share**2) * 10000 for c in countries) if complete else None,
        countries=countries,
        input_evidence_ids=sorted(set(ids)),
        assumptions=[
            "Shares use USD import values within one matching period/classification/scope.",
            "This is retrospective context only; no trade/FDA values enter the July 2023 inventory simulation.",
        ],
        limitations=[
            "Two observed partners do not establish the largest partners or a global concentration index.",
            "No national denominator or prior-period value is fabricated. Source revisions have their own retrieval cutoffs.",
        ],
    )
