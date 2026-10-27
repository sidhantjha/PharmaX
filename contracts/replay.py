"""6a.1 replay admission boundary. No acquisition, inference or physical action.

Admission validates declared metadata, not actual bytes or publisher authenticity.
6B must verify hashes/extraction before constructing validated source revisions.
"""

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from .data import (
    Identifier, ReplayConsumption, ReplayHospital, ReplayInventory,
    ReplayProduct, ReplayPurchaseOrder, ReplayShipment,
    ReplayPurchasing, ReplayAcknowledgment,
)
from .models import Contract, SourceEvidence
from .risk import DailyStockPoint
from .operational_claims import EvidenceVerification

SHA256 = r"^[a-f0-9]{64}$"
Scenario = Literal["helene-north-cove"]
ReplayMode = Literal["strict-historical", "reconstructed-historical-simulation"]
Feed = Literal["catalog", "inventory", "consumption", "purchasing", "purchase_orders",
               "acknowledgments", "shipments", "facilities", "suppliers"]


def document_time(day: date) -> datetime:
    """Conservative date-only availability: exclusive end of US Eastern day."""
    return datetime.combine(day + timedelta(days=1), time.min, ZoneInfo("America/New_York"))


class SourceRevision(Contract):
    schema_version: Literal["6a.1"] = "6a.1"
    revision_id: Identifier
    publisher: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    issue_date: date
    evidence: SourceEvidence
    availability_proof: SourceEvidence | None = None
    extraction_status: Literal["pending", "validated", "quarantined"]
    extractor_version: str = Field(min_length=1)
    validation_reference: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def lineage(self):
        ev = self.evidence
        if ev.kind != "observed" or not ev.raw_ref or not ev.source_record_id:
            raise ValueError("Source revision requires observed raw document identity")
        if self.document_id != ev.source_record_id:
            raise ValueError("Document identity must match original source record")
        if ev.published_on != self.issue_date or self.issue_date > ev.observed_at.date():
            raise ValueError("Issue date must match evidence publication and precede retrieval")
        if ev.version_available_at and ev.version_available_at < document_time(self.issue_date):
            raise ValueError("Date-only issue cannot assert intraday version availability")
        proof = self.availability_proof
        if ev.version_available_at is not None:
            if (proof is None or proof.kind != "observed" or not proof.raw_ref
                    or proof.evidence_id == ev.evidence_id or ev.evidence_id not in proof.parent_ids
                    or proof.content_sha256 == ev.content_sha256 or proof.raw_ref == ev.raw_ref):
                raise ValueError("Version availability requires independent proof bound to this evidence")
        elif proof is not None:
            raise ValueError("Availability proof requires an attested version timestamp")
        if self.extraction_status == "validated" and not self.validation_reference:
            raise ValueError("Validated extraction requires a validation reference")
        return self


class AllocationNotice(Contract):
    schema_version: Literal["6a.1"] = "6a.1"
    notice_id: Identifier
    source_revision_id: Identifier
    source_sha256: str = Field(pattern=SHA256)
    product_id: Identifier
    original_baxter_code: str = Field(min_length=1)
    original_ndc: str = Field(min_length=1)
    container_ml: float = Field(gt=0)
    quantity_unit: Literal["bag"] = "bag"
    allocation_percent: float = Field(ge=0, le=100)
    effective_on: date
    kind: Literal["observed", "projected"]
    exact_claim: str = Field(min_length=1)
    customer_class: str = Field(min_length=1)
    baseline_start: date | None = None
    baseline_end: date | None = None
    page: int = Field(ge=1, strict=True)
    table_location: str = Field(min_length=1)
    validation_status: Literal["validated", "quarantined"]

    @model_validator(mode="after")
    def baseline(self):
        if (self.baseline_start is None) != (self.baseline_end is None):
            raise ValueError("Baseline dates must both be known or unknown")
        if self.baseline_start and self.baseline_start > self.baseline_end:
            raise ValueError("Reversed allocation baseline")
        return self


class OperationalImportReceipt(Contract):
    """Future CSV/EDI/FHIR adapters must resolve into this simulated boundary."""

    schema_version: Literal["6a.1"] = "6a.1"
    import_id: Identifier
    scenario_id: Scenario
    feed: Feed
    format: Literal["csv", "edi-850", "edi-855", "edi-856", "fhir-r5-inventoryreport"]
    source_path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=SHA256)
    exported_at: AwareDatetime
    effective_at: AwareDatetime
    modeled_available_at: AwareDatetime
    evidence: SourceEvidence
    mapping_version: str = Field(min_length=1)
    record_ids: list[Identifier] = Field(min_length=1, max_length=10000)
    validation_status: Literal["validated", "quarantined"]
    assumptions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def provenance(self):
        ev = self.evidence
        if ev.kind != "simulated" or not ev.raw_ref or self.content_sha256 != ev.content_sha256:
            raise ValueError("Operational imports require simulated hash-bound provenance")
        if self.effective_at > self.exported_at or self.exported_at > ev.observed_at:
            raise ValueError("Incoherent effective/export/retrieval clocks")
        if not self.effective_at <= self.modeled_available_at <= self.exported_at:
            raise ValueError("Simulated availability must follow effective time and precede actual export")
        if len(set(self.record_ids)) != len(self.record_ids):
            raise ValueError("Duplicate imported record identifiers")
        allowed = {"edi-850": "purchase_orders", "edi-855": "acknowledgments",
                   "edi-856": "shipments", "fhir-r5-inventoryreport": "inventory"}
        if self.format != "csv" and allowed[self.format] != self.feed:
            raise ValueError("Format is incompatible with operational feed")
        return self


class ScenarioOperations(Contract):
    """Independent canonical rows; no manufacturer notice required to construct."""

    schema_version: Literal["6a.1"] = "6a.1"
    scenario_id: Scenario
    network_id: Literal["sim-nc-health-system"]
    products: list[ReplayProduct] = Field(min_length=1)
    hospitals: list[ReplayHospital] = Field(min_length=3, max_length=3)
    inventory: list[ReplayInventory]
    consumption: list[ReplayConsumption]
    purchase_orders: list[ReplayPurchaseOrder]
    shipments: list[ReplayShipment]
    purchasing: list[ReplayPurchasing]
    acknowledgments: list[ReplayAcknowledgment]

    @model_validator(mode="after")
    def scope(self):
        hospitals = {h.hospital_id for h in self.hospitals}
        if hospitals != {"sim-nc-central", "sim-nc-north", "sim-nc-south"}:
            raise ValueError("Require the declared fictional NC network")
        products = {p.product_id: p for p in self.products}
        if len(products) != len(self.products):
            raise ValueError("Duplicate product identifier")
        aliases = {}
        for p in self.products:
            for a in p.aliases:
                key = (a.scheme, a.normalized)
                if key in aliases and aliases[key] != p.product_id:
                    raise ValueError("Cross-product alias collision")
                aliases[key] = p.product_id
        for rows, key in ((self.inventory, "inventory_id"), (self.consumption, "consumption_id"),
                          (self.purchase_orders, "purchase_order_id"), (self.shipments, "shipment_id"),
                          (self.purchasing, "purchasing_id"), (self.acknowledgments, "acknowledgment_id")):
            if len({getattr(r, key) for r in rows}) != len(rows):
                raise ValueError("Duplicate operational identifier")
            for row in rows:
                if row.evidence.kind != "simulated":
                    raise ValueError("Operational rows must remain simulated")
                if row.hospital_id not in hospitals or row.product_id not in products:
                    raise ValueError("Cross-scenario facility/product identity")
                if row.quantity_unit != products[row.product_id].quantity_unit:
                    raise ValueError("Operational unit disagrees with product")
        orders = {p.purchase_order_id: p for p in self.purchase_orders}
        totals = {}
        for row in [*self.shipments, *self.acknowledgments]:
            po = orders.get(row.purchase_order_id)
            if po is None or any(getattr(row, k) != getattr(po, k) for k in (
                "order_line_id", "account_id", "hospital_id", "supplier_id", "product_id", "quantity_unit"
            )):
                raise ValueError("Unmatched operational order lineage")
            if row.quantity > po.quantity:
                raise ValueError("Supply exceeds ordered quantity")
        for ship in self.shipments:
            totals[ship.purchase_order_id] = totals.get(ship.purchase_order_id, 0) + ship.quantity
            if totals[ship.purchase_order_id] > orders[ship.purchase_order_id].quantity:
                raise ValueError("Shipment quantities exceed order")
        return self


class ReplayCheckpoint(Contract):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["6a.1"] = "6a.1"
    checkpoint_id: Identifier
    scenario_id: Scenario
    cutoff: AwareDatetime
    mode: ReplayMode
    policy_version: Literal["helene-admission-v1"] = "helene-admission-v1"
    source_revision_ids: tuple[Identifier, ...]
    import_ids: tuple[Identifier, ...]
    parent_checkpoint_id: Identifier | None = None
    parent_cutoff: AwareDatetime | None = None
    parent_sha256: str | None = Field(default=None, pattern=SHA256)
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def parent(self):
        parent = (self.parent_checkpoint_id, self.parent_cutoff, self.parent_sha256)
        if any(v is not None for v in parent) and not all(v is not None for v in parent):
            raise ValueError("Parent checkpoint requires identity, cutoff and immutable hash")
        if self.parent_checkpoint_id == self.checkpoint_id or (
            self.parent_cutoff and self.parent_cutoff > self.cutoff
        ):
            raise ValueError("Self/future parent checkpoint")
        for ids in (self.source_revision_ids, self.import_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate checkpoint references")
        if self.mode == "reconstructed-historical-simulation" and not self.limitations:
            raise ValueError("Reconstruction requires persistent limitations")
        return self


class ScenarioReplayRequest(Contract):
    schema_version: Literal["6a.1"] = "6a.1"
    checkpoint: ReplayCheckpoint
    reconstruction_acknowledged: bool = False
    sources: list[SourceRevision] = Field(max_length=100)
    allocations: list[AllocationNotice] = Field(default_factory=list, max_length=1000)
    imports: list[OperationalImportReceipt] = Field(default_factory=list, max_length=100)
    parent_evidence: list[SourceEvidence] = Field(default_factory=list, max_length=1000)
    products: list[ReplayProduct] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def admission(self):
        cp = self.checkpoint
        reconstructed = cp.mode == "reconstructed-historical-simulation"
        if reconstructed and not self.reconstruction_acknowledged:
            raise ValueError("Reconstructed replay requires explicit acknowledgment")
        sources = {s.revision_id: s for s in self.sources}
        imports = {r.import_id: r for r in self.imports}
        if len(sources) != len(self.sources) or len(imports) != len(self.imports):
            raise ValueError("Duplicate source/import identifiers")
        if set(sources) != set(cp.source_revision_ids) or set(imports) != set(cp.import_ids):
            raise ValueError("Checkpoint references must exactly match admitted versions")
        evidence = {}
        revisions = {}
        for s in self.sources:
            if s.extraction_status != "validated":
                raise ValueError("Pending/quarantined source cannot enter replay")
            if s.evidence.evidence_id in revisions:
                raise ValueError("Duplicate source evidence revision")
            revisions[s.evidence.evidence_id] = s
        all_evidence = [s.evidence for s in self.sources] + self.parent_evidence
        all_evidence += [s.availability_proof for s in self.sources if s.availability_proof]
        all_evidence += [r.evidence for r in self.imports]
        all_evidence += [p.evidence for p in self.products]
        if len({e.evidence_id for e in self.parent_evidence}) != len(self.parent_evidence):
            raise ValueError("Duplicate parent evidence identifier")
        for ev in all_evidence:
            if ev.evidence_id in evidence and evidence[ev.evidence_id] != ev:
                raise ValueError("Conflicting evidence identifier")
            evidence[ev.evidence_id] = ev
        proof_ids = {s.availability_proof.evidence_id for s in self.sources if s.availability_proof}
        modeled_clocks = {}
        for receipt in self.imports:
            key = receipt.evidence.evidence_id
            if key in modeled_clocks and modeled_clocks[key] != receipt.modeled_available_at:
                raise ValueError("Conflicting simulated availability clocks")
            modeled_clocks[key] = receipt.modeled_available_at
        visited, active = set(), set()

        def visit(key):
            if key in active:
                raise ValueError("Cyclic evidence parents")
            if len(active) >= 100:
                raise ValueError("Evidence lineage depth budget exceeded")
            if key in visited:
                return
            if key not in evidence:
                raise ValueError("Missing evidence parent")
            active.add(key)
            ev = evidence[key]
            revision = revisions.get(key)
            # Reconstructed assumptions apply only to the selected reviewed documents.
            available = (document_time(revision.issue_date) if reconstructed and revision
                         else modeled_clocks.get(key, ev.version_available_at or ev.observed_at))
            if available > cp.cutoff:
                raise ValueError("Future evidence/parent after checkpoint cutoff")
            if not reconstructed and revision and not revision.availability_proof:
                raise ValueError("Strict historical availability is unproven")
            if (ev.kind == "observed" and ev.observed_at > cp.cutoff
                    and not revision and key not in proof_ids):
                raise ValueError("Historical parent availability requires a reviewed source revision")
            if ev.published_on and document_time(ev.published_on) > cp.cutoff:
                raise ValueError("Future date-only publication")
            if ev.updated_on and document_time(ev.updated_on) > cp.cutoff:
                raise ValueError("Future evidence revision")
            for parent in ev.parent_ids:
                visit(parent)
            active.remove(key)
            visited.add(key)

        for key in evidence:
            visit(key)
        for receipt in self.imports:
            if (receipt.scenario_id != cp.scenario_id or receipt.validation_status != "validated"
                    or receipt.modeled_available_at > cp.cutoff):
                raise ValueError("Unavailable or cross-scenario operational export")
        products = {p.product_id: p for p in self.products}
        if len(products) != len(self.products):
            raise ValueError("Duplicate replay product")
        rows, ids = {}, set()
        for row in self.allocations:
            if row.notice_id in ids:
                raise ValueError("Duplicate allocation notice")
            ids.add(row.notice_id)
            source = sources.get(row.source_revision_id)
            product = products.get(row.product_id)
            if (source is None or source.evidence.content_sha256 != row.source_sha256
                    or row.validation_status != "validated"):
                raise ValueError("Allocation source/hash/validation mismatch")
            if product is None or product.quantity_unit != row.quantity_unit or product.container_ml != row.container_ml:
                raise ValueError("Unmatched allocation product/presentation")
            for scheme, original in (("baxter", row.original_baxter_code), ("ndc", row.original_ndc)):
                if not any(a.scheme == scheme and a.original == original for a in product.aliases):
                    raise ValueError("Unmatched original product identifier")
            if row.kind == "observed" and row.effective_on > source.issue_date:
                raise ValueError("Future allocation must remain projected")
            key = (row.product_id, row.customer_class, row.effective_on)
            if key in rows:
                raise ValueError("Duplicate or contradictory allocation values require reconciliation")
            rows[key] = row.allocation_percent
        return self


class HeleneForecast(Contract):
    hospital_id: Identifier
    quantity_unit: Literal['bag'] = 'bag'
    as_of: date
    end_on: date
    expected_daily_demand: int = Field(ge=0)
    initial_usable: int = Field(ge=0)
    total_received: int = Field(ge=0)
    total_unmet: int = Field(ge=0)
    projected_stockout_date: date | None
    analysis_id: str = Field(pattern=SHA256)
    points: list[DailyStockPoint]
    evidence_ids: list[str]
    limitations: list[str]


class HeleneCase(Contract):
    verification: EvidenceVerification | None = None
    schema_version: Literal['6.1'] = '6.1'
    investigation_id: Identifier
    scenario: Scenario = 'helene-north-cove'
    checkpoint: ReplayCheckpoint
    admission: ScenarioReplayRequest
    operations: ScenarioOperations
    dataset_version: str = Field(pattern=SHA256)
    created_at: AwareDatetime
    parent_id: Identifier | None
    evidence: list[SourceEvidence]
    forecasts: list[HeleneForecast]
    mitigated: list[HeleneForecast]
    candidates: list[dict]
    selected: list[str]
    actions: list[dict]
    decisions: list[dict]
    memory: dict
    reconciliation: list[dict]
    unresolved: list[str]
    map_nodes: list[dict]
    classification: Literal['observed-documents+simulated-operations+derived-results'] = (
        'observed-documents+simulated-operations+derived-results')
