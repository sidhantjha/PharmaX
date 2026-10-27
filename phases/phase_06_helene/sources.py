"""Explicit connected acquisition and deterministic, hash-checked cached PDF extraction."""

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

from pypdf import PdfReader

from contracts.models import SourceEvidence
from contracts.replay import AllocationNotice, SourceRevision
from phases.phase_01_foundation.http import make_client, ServiceError

ROOT = Path(__file__).parent / "fixtures/manufacturer"
URLS = {
    "2024-09-29": "https://www.fda.gov/media/182564/download",
    "2024-10-01": "https://nutritioncare.org/wp-content/uploads/2025/01/241001_Allocation-Letter-Baxter.pdf",
    "2024-10-09": "https://nutritioncare.org/wp-content/uploads/2025/01/241009_Baxter-Allocation-Letter.pdf",
    "2024-11-11": "https://nutritioncare.org/wp-content/uploads/2025/01/241111_Baxter-Allocation-Letter_HH.pdf",
}
PRODUCT_ID = "sim-baxter-saline-1000"


def acquire(root=ROOT):
    """One request per allowlisted document, <=4 MiB each; never used during replay."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "manifest.json").exists():
        raise ValueError("SOURCE_MANIFEST_ALREADY_EXISTS")
    records = []
    with make_client() as client:
        for day, url in URLS.items():
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise ServiceError(f"HTTP_{response.status_code}")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 4_194_304:
                        raise ServiceError("RESPONSE_TOO_LARGE")
            raw = bytes(data)
            if not raw.startswith(b"%PDF-"):
                raise ValueError("INVALID_SOURCE_PDF")
            digest = sha256(raw).hexdigest()
            name = digest + ".pdf"
            (root / name).write_bytes(raw)
            records.append(
                dict(
                    issue_date=day,
                    url=url,
                    sha256=digest,
                    file=name,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    publisher="Baxter",
                    custody="2024 exact-version availability unproven; current public rehost only",
                )
            )
    (root / "manifest.json").write_text(json.dumps(records, indent=2) + "\n")
    return records


def admit_source(record, root=ROOT):
    path = Path(root) / record["file"]
    if path.parent.resolve() != Path(root).resolve():
        raise ValueError("INVALID_SOURCE_PATH")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != record["sha256"] or not raw.startswith(b"%PDF-"):
        raise ValueError("SOURCE_HASH_MISMATCH")
    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 12:
        raise ValueError("SOURCE_PDF_UNSUPPORTED")
    pages = [p.extract_text(extraction_mode="layout") for p in reader.pages]
    day = record["issue_date"]
    dates = {
        "2024-09-29": r"(September\s+29,?\s+2024|9/29/2024)",
        "2024-10-01": r"October\s+0?1,?\s+2024",
        "2024-10-09": r"October\s+9,?\s+2024",
        "2024-11-11": r"November\s+11,?\s+2024",
    }
    if day not in dates or not re.search(dates[day], pages[0]) or "North Cove" not in pages[0]:
        raise ValueError("SOURCE_DOCUMENT_IDENTITY_MISMATCH")
    ev = SourceEvidence(
        evidence_id="baxter-" + day,
        kind="observed",
        source="Baxter manufacturer letter",
        source_record_id="baxter-" + day,
        source_url=record["url"],
        observed_at=record["retrieved_at"],
        published_on=day,
        content_sha256=record["sha256"],
        raw_ref=str(path),
        note=record["custody"],
    )
    revision = SourceRevision(
        revision_id="baxter-" + day,
        publisher="Baxter",
        document_id=ev.source_record_id,
        issue_date=day,
        evidence=ev,
        extraction_status="validated",
        extractor_version="pypdf-6.21.2-layout-v1",
        validation_reference="phase6-exact-row-and-date-validation-v1",
    )
    return revision, pages


def extract_allocation(revision, pages):
    if revision.issue_date.isoformat() == "2024-09-29":
        text = pages[0]
        if not all(s in text for s in ("closed for production", "48 hours", "allocations")):
            raise ValueError("UNSUPPORTED_DISRUPTION_CLAIM")
        return []
    matches = []
    for page, text in enumerate(pages, 1):
        # Layout extraction preserves the row; wrapped descriptions may continue below it.
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if re.search(r"\b2B1324X\b", line):
                block = line + "\n" + "\n".join(lines[index + 1 : index + 3])
                block = re.split(r"\b2B(?!1324X)\d{4}[A-Z]?\b", block)[0]
                compact = re.sub(r"\s+", " ", block)
                if not (
                    re.search(r"00338-", compact)
                    and re.search(r"0049-04", compact)
                    and re.search(r"1000\s*ML", compact, re.I)
                    and "0.9%" in compact
                ):
                    raise ValueError("ALLOCATION_PRODUCT_ROW_AMBIGUOUS")
                # Percentages must be on the exact product row, not a neighboring product.
                values = re.findall(r"(?<![\d.])(\d{1,3})\s*%", line)
                if not values:
                    # Some PDFs wrap NDC/percentage onto the next line before the next code.
                    tail = re.split(r"\b2[A-Z]\d{4}[A-Z]?\b", block[len(line) :])[0]
                    values = re.findall(r"(?<![\d.])(\d{1,3})\s*%", tail)
                matches.append((page, block.strip(), values))
    if len(matches) != 1:
        raise ValueError("MISSING_OR_DUPLICATE_PRODUCT_ROW")
    page, exact, values = matches[0]
    november = revision.issue_date.isoformat() == "2024-11-11"
    if len(values) != (4 if november else 1):
        raise ValueError("ALLOCATION_COLUMNS_AMBIGUOUS")
    dates = ["2024-11-11", "2024-11-25", "2024-12-16", "2024-12-30"] if november else [revision.issue_date]
    rows = []
    for index, (effective, value) in enumerate(zip(dates, values)):
        rows.append(
            AllocationNotice(
                notice_id=revision.revision_id + f"-row-{index}",
                source_revision_id=revision.revision_id,
                source_sha256=revision.evidence.content_sha256,
                product_id=PRODUCT_ID,
                original_baxter_code="2B1324X",
                original_ndc="00338-0049-04",
                container_ml=1000,
                allocation_percent=int(value),
                effective_on=effective,
                kind="projected" if november and index else "observed",
                exact_claim=exact,
                customer_class="US direct customers/distribution partners; not designated children hospitals",
                baseline_start="2024-03-01" if not november else None,
                baseline_end="2024-08-31" if not november else None,
                page=page,
                table_location="Table 1 product 2B1324X"
                if not november
                else "Current / anticipated allocation columns",
                validation_status="validated",
            )
        )
    if november and not re.search(r"1\s+to\s+2.week lag", pages[0]):
        raise ValueError("DISTRIBUTION_LAG_NOT_FOUND")
    return rows


def load_sources(root=ROOT):
    result = []
    for record in json.loads((Path(root) / "manifest.json").read_text()):
        revision, pages = admit_source(record, root)
        result.append((revision, extract_allocation(revision, pages), pages))
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--acquire", action="store_true")
    args = parser.parse_args()
    if args.acquire:
        acquire()
    for revision, rows, pages in load_sources():
        print(
            revision.revision_id,
            revision.evidence.content_sha256,
            len(pages),
            [(r.page, r.allocation_percent, str(r.effective_on), r.kind) for r in rows],
        )
