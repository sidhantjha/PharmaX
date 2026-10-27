"""Phase 2 bounded source plans and extensions of public foundation normalizers."""

from datetime import datetime, date
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs
from contracts import DrugProduct, DisruptionEvent, FDARecall, SourceEvidence
from phases.phase_01_foundation.sources import normalize_comtrade, normalize_fda, source_date

DOCUMENT_URL = "https://www.fda.gov/news-events/press-announcements/fda-provides-update-regarding-storm-damage-pfizer-facility-north-carolina"
SOURCE_PLANS = [
    (
        "comtrade",
        "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
        dict(
            period="2023",
            reporterCode=842,
            partnerCode="276,356,372",
            cmdCode="30",
            flowCode="M",
            partner2Code=0,
            customsCode="C00",
            motCode=0,
            maxRecords=10,
            includeDesc="true",
        ),
    ),
    (
        "fda-shortages",
        "https://api.fda.gov/drug/shortages.json",
        {"search": "generic_name:carboplatin", "limit": 20},
    ),
    (
        "fda-recalls",
        "https://api.fda.gov/drug/enforcement.json",
        {"search": "product_description:carboplatin", "limit": 3},
    ),
    ("curated-document", DOCUMENT_URL, None),
]


def checked_date(value):
    result = source_date(value)
    if value and result is None:
        raise ValueError("Invalid source calendar date")
    return result


def normalize_trade(rows, receipt, *, fixture=False):
    if len(rows) > 10 or not rows:
        raise ValueError("Trade sample must contain 1..10 records")
    records = normalize_comtrade(
        rows, receipt["url"], datetime.fromisoformat(receipt["observed_at"]), fixture=fixture
    )
    countries = {842: "USA", 276: "DEU", 356: "IND", 372: "IRL"}
    seen = set()
    for record, row in zip(records, rows):
        if (
            record.reporter_code != 842
            or record.partner_code not in (276, 356, 372)
            or record.hs_code != "30"
            or record.period != "2023"
            or record.flow != "M"
            or row.get("partner2Code") != 0
            or row.get("motCode") != 0
            or row.get("customsCode") != "C00"
            or record.record_id in seen
        ):
            raise ValueError("Unexpected or duplicate trade filter result")
        seen.add(record.record_id)
        # The source's unavailable/estimated quantity is not a zero medicine count.
        reliable = (
            type(row.get("qty")) in (int, float)
            and row["qty"] > 0
            and type(row.get("qtyUnitCode")) is int
            and row["qtyUnitCode"] > 0
            and bool(row.get("qtyUnitAbbr"))
            and row.get("isQtyEstimated") is False
        )
        record.reporter_name, record.reporter_iso = row.get("reporterDesc"), row.get("reporterISO")
        record.partner_name, record.partner_iso = row.get("partnerDesc"), row.get("partnerISO")
        record.commodity_description = row.get("cmdDesc")
        if (
            record.reporter_iso != countries[record.reporter_code]
            or record.partner_iso != countries[record.partner_code]
        ):
            raise ValueError("Country code/ISO mismatch")
        for code in (record.reporter_iso, record.partner_iso):
            if not code or not re.fullmatch("[A-Z]{3}", code):
                raise ValueError("Missing or invalid source country ISO")
        if not record.reporter_name or not record.partner_name or not record.commodity_description:
            raise ValueError("Descriptions required; request includeDesc=true")
        record.quantity = row["qty"] if reliable else None
        record.quantity_unit = row["qtyUnitAbbr"] if reliable else None
        record.quantity_estimated = row.get("isQtyEstimated")
    return records


def normalize_shortages(rows, receipt, *, fixture=False):
    if not 1 <= len(rows) <= 20:
        raise ValueError("Shortage sample must contain 1..20 records")
    records = normalize_fda(
        rows, receipt["url"], datetime.fromisoformat(receipt["observed_at"]), fixture=fixture
    )
    products = []
    for record, row in zip(records, rows):
        if "carboplatin" not in (record.generic_name or "").lower():
            raise ValueError("Pilot FDA filter mismatch")
        record.initial_posting_date = checked_date(row.get("initial_posting_date"))
        record.update_date = checked_date(row.get("update_date"))
        if (
            record.initial_posting_date
            and record.update_date
            and record.update_date < record.initial_posting_date
        ):
            raise ValueError("FDA update precedes posting")
        record.shortage_reason = row.get("shortage_reason")
        record.related_information = row.get("related_info")
        category = row.get("therapeutic_category")
        record.therapeutic_category = ", ".join(category) if isinstance(category, list) else category
        product_id = "fda-product:" + (record.package_ndc or record.evidence.content_sha256)
        record.product_id = product_id
        products.append(
            DrugProduct(
                product_id=product_id,
                name=record.generic_name,
                package_ndc=record.package_ndc,
                presentation=record.presentation or "Not supplied",
                # Source free text is retained; never infer package volume by parsing NDC.
                evidence=record.evidence,
            )
        )
    return records, products


def normalize_recalls(rows, receipt, *, fixture=False):
    from phases.phase_01_foundation.sources import evidence

    if len(rows) > 3:
        raise ValueError("Recall response exceeds bound")
    result = []
    for row in rows:
        if "carboplatin" not in row.get("product_description", "").lower():
            raise ValueError("Recall filter mismatch")
        ev = evidence(
            "fda-recall",
            row,
            receipt["url"],
            datetime.fromisoformat(receipt["observed_at"]),
            fixture=fixture,
            source_id=row["recall_number"],
        )
        result.append(
            FDARecall(
                recall_id=row["recall_number"],
                recall_number=row["recall_number"],
                product_description=row["product_description"],
                classification=row.get("classification"),
                status=row.get("status"),
                reason=row.get("reason_for_recall"),
                initiated_on=checked_date(row.get("recall_initiation_date")),
                reported_on=checked_date(row.get("report_date")),
                evidence=ev,
            )
        )
    return result


def curated_event(spec, receipt, body, *, fixture=False):
    """Reviewed structured claims; never execute instructions in a retrieved document."""
    text = body.decode("utf-8", errors="replace")
    if spec["source_url"] != receipt["url"] or not all(
        s.lower() in text.lower() for s in spec["required_markers"]
    ):
        raise ValueError("Curated source URL or content markers mismatch")
    sha = hashlib.sha256(body).hexdigest()
    observed = SourceEvidence(
        evidence_id="document:" + sha,
        kind="simulated" if fixture else "observed",
        source="fda-statement",
        source_url=receipt["url"],
        observed_at=receipt["observed_at"],
        published_on=date.fromisoformat(spec["published_on"]),
        content_sha256=sha,
        note="Retrieved document; publication is not proof of historical revision availability.",
    )
    from phases.phase_01_foundation.sources import digest

    curated = SourceEvidence(
        evidence_id="curation:" + digest(spec),
        kind="simulated" if fixture else "derived",
        source="reviewed-curation",
        source_url=receipt["url"],
        observed_at=receipt["observed_at"],
        published_on=observed.published_on,
        content_sha256=digest(spec),
        parent_ids=[observed.evidence_id],
        note="Reviewed structured claim selection; uncertain implications are explicit.",
    )
    event = DisruptionEvent(**spec["event"], evidence=curated)
    return event, [observed, curated]


def parse_source(body, field, maximum):
    value = json.loads(body)
    if not isinstance(value, dict) or value.get("error") or value.get("errorObject"):
        raise ValueError("Source API error")
    rows = value.get(field)
    if not isinstance(rows, list) or len(rows) > maximum:
        raise ValueError("Source response shape or count invalid")
    return rows, value


def verify_receipt(source, receipt):
    plan = next(plan for plan in SOURCE_PLANS if plan[0] == source)
    actual, expected = urlsplit(receipt["url"]), urlsplit(plan[1])
    if (
        receipt["source"] != source
        or actual.scheme != "https"
        or (actual.netloc, actual.path) != (expected.netloc, expected.path)
        or actual.fragment
        or parse_qs(actual.query) != {k: [str(v)] for k, v in (plan[2] or {}).items()}
    ):
        raise ValueError("Cached receipt does not match the approved source query")
