"""Final bounded Helene profiles for the existing native ModelSession.

All arithmetic and pharmacy gates remain in the existing replay/workflow tools.
No model output can dispatch an action or replace a forecast.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import threading
from time import monotonic
from typing import Literal
from uuid import uuid4
import httpx
from pydantic import Field, create_model
from contracts.models import Contract
from contracts.operator import ExecutionTask
from phases.phase_01_foundation.http import ServiceError
from phases.phase_04_agents.models import Finding
from phases.phase_06_helene.specialists import remember
from phases.phase_07_reconciliation.pipeline import verify_case
from phases.phase_08_operations.specialists import OperationalTools
from phases.phase_08_operations.workflow import WorkflowStore
from .execution import ExecutionTrace, concurrent_reads
from .helene_model_verification import RoleExecution
from .integration import RESPONSIBILITIES
from .live_specialists import Empty, ModelSession
from .service import OperatorService
from .verification_budget import VerificationBudget
from .verified_runtime import MODEL, reviewed_authorization

WAVES = [
    ("news",),
    ("verification", "disruption"),
    ("dependency", "trade"),
    ("hospital",),
    ("memory",),
    ("mitigation",),
    ("feasibility", "clinical"),
    ("actions",),
    ("coordinator",),
]
SCOPES = {
    "news": ("source_provenance_limited", "evidence_custody_unproven"),
    "verification": (
        "source_provenance_limited",
        "evidence_custody_unproven",
        "evidence_reconciliation_required",
    ),
    "disruption": ("source_provenance_limited", "evidence_custody_unproven"),
    "dependency": ("simulated_hospital_records", "evidence_custody_unproven", "dependency_evidence_limited"),
    "trade": ("trade_unavailable", "insufficient_trade_evidence"),
    "hospital": ("simulated_hospital_records", "inventory_shortage_supported", "inventory_evidence_limited"),
    "memory": ("memory_recalled", "human_review_required"),
    "mitigation": ("human_review_required", "conditional_recommendation"),
    "feasibility": ("human_review_required", "unsafe_alternatives_rejected"),
    "clinical": ("clinical_model_unavailable", "insufficient_clinical_evidence"),
    "actions": ("human_review_required", "draft_only"),
    "coordinator": ("human_review_required", "conditional_recommendation"),
}
TOOLS = {
    "news": ("news_source_evidence", "manufacturer.read", "verification"),
    "verification": ("verification_reconcile", "evidence.findings", "verification"),
    "disruption": ("disruption_allocations", "manufacturer.read", "verification"),
    "dependency": ("dependency_identity", "identity.resolve", "dependency"),
    "trade": ("trade_evidence_lookup", "evidence.records", "verification"),
    "hospital": ("hospital_inventory_forecast", "readiness.inspect", "hospital"),
    "memory": ("memory_write_and_recall", "hmem.exchange", None),
    "mitigation": ("mitigation_compare", "mitigation.read", "mitigation"),
    "feasibility": ("feasibility_critic", "supply.validated", "mitigation"),
    "clinical": ("clinical_support_check", "forecast.read", "hospital"),
    "actions": ("actions_review_drafts", "actions.inspect", "mitigation"),
    "coordinator": ("coordinator_synthesis", "mitigation.read", "mitigation"),
}


class CandidateInput(Contract):
    candidate_id: str = Field(min_length=1, max_length=120)


def schema(role, result=None):
    facts = set(result["fact_keys"]) if result else set()
    evidence = set(result["evidence_ids"]) if result else set()
    for prior in (result or {}).get("predecessor_findings", []):
        facts.update(prior["fact_keys"])
        evidence.update(prior["evidence_ids"])
    fact_type = Literal[tuple(sorted(facts))] if facts else str
    evidence_type = Literal[tuple(sorted(evidence))] if evidence else str
    return create_model(
        role.title() + "Finding",
        __base__=Contract,
        observation=(Literal[SCOPES[role]], ...),
        evidence_ids=(list[evidence_type], Field(min_length=1, max_length=6)),
        fact_keys=(
            list[fact_type],
            Field(
                min_length=1,
                max_length=4,
                description="Choose at most four separate supported fact keys. Never combine keys into one string.",
            ),
        ),
        candidate_id=(str | None, None),
        memory_ids=(list[str], Field(default_factory=list, max_length=5)),
    )


def validate_finding(role, answer, result):
    delivered_evidence = set(result["evidence_ids"])
    delivered_facts = set(result["fact_keys"])
    for prior in result.get("predecessor_findings", []):
        delivered_evidence.update(prior["evidence_ids"])
        delivered_facts.update(prior["fact_keys"])
    if (
        not set(answer.evidence_ids) <= delivered_evidence
        or not set(answer.fact_keys) <= delivered_facts
        or not set(answer.memory_ids) <= set(result["memory_ids"])
    ):
        raise ValueError("FINDING_CITATION_OR_FACT_UNSUPPORTED")
    if not set(answer.fact_keys) & set(result["fact_keys"]):
        raise ValueError("ROLE_FINDING_REQUIRES_DIRECT_SUPPORTED_FACT")
    if (
        role in {"memory", "mitigation", "feasibility", "coordinator"}
        and result["memory_ids"]
        and not answer.memory_ids
    ):
        raise ValueError("RECALLED_CONTEXT_NOT_ACKNOWLEDGED")
    feasible = {c["candidate_id"] for c in result.get("candidates", []) if c["feasible"]}
    if answer.candidate_id is not None and (
        role not in {"mitigation", "feasibility", "coordinator", "actions"}
        or answer.candidate_id not in feasible
        or answer.candidate_id in result.get("excluded_due_to_recalled_rejection", [])
    ):
        raise ValueError("CANDIDATE_UNSAFE_OR_NOT_SUPPORTED")
    if (
        role == "actions"
        and answer.candidate_id is not None
        and answer.candidate_id not in {d["candidate_id"] for d in result.get("drafts", [])}
    ):
        raise ValueError("ACTION_CANDIDATE_HAS_NO_REVIEW_ONLY_DRAFT")
    if role in {"mitigation", "coordinator"} and feasible and answer.candidate_id is None:
        raise ValueError("CALCULATED_OPTION_REQUIRES_EXPLICIT_SELECTION")


class FinalProfiles:
    def __init__(self, case, proof, service, root, trace):
        self.case, self.proof, self.service, self.root, self.trace = case, proof, service, Path(root), trace
        self.workflow = WorkflowStore(service.store)
        self.view = self.workflow.view(case)
        self.read_case = case.model_copy(update={"verification": verify_case(case)})
        self.completed = {}
        self.memory = {}
        self.lock = threading.Lock()
        self.registry = {e.evidence_id: e for e in case.evidence}
        self.basis = "replay-dataset-" + case.dataset_version
        if self.basis not in self.registry:
            self.basis = case.evidence[-1].evidence_id

    def predecessors(self):
        with self.lock:
            return [dict(role=r, **a) for r, a in self.completed.items()]

    def tool(self, role, args):
        wire, canonical, scope = TOOLS[role]
        raw = (
            OperationalTools(self.read_case, self.workflow).call(scope, canonical)
            if canonical != "hmem.exchange"
            else None
        )
        evidence = [self.basis]
        facts = []
        result = dict(
            role=role,
            canonical_tool=canonical,
            simulated_hospital_data=True,
            human_review_required=True,
            zetaris_trace_id=self.proof["trace_id"],
            zetaris_result_sha256=self.proof["result_sha256"],
            predecessor_findings=self.predecessors()[-4:],
        )
        sources = self.proof["source_lineage"]["manufacturer"]
        if role in {"news", "verification", "disruption"}:
            result["manufacturer_sources"] = [
                dict(
                    id=s["evidence"]["evidence_id"],
                    publisher=s["publisher"],
                    issue_date=s["issue_date"],
                    sha256=s["evidence"]["content_sha256"],
                    historical_custody="unproven",
                )
                for s in sources
            ]
            result["allocation_evidence"] = [
                a.model_dump(mode="json") for a in self.case.admission.allocations
            ][:3]
            evidence = [s["evidence"]["evidence_id"] for s in sources][:6]
            facts = ["manufacturer_provenance_retained", "historical_version_custody_unproven"]
            if role == "verification":
                result["reconciliation"] = [
                    dict(category=f.category, reason=f.reason)
                    for f in self.read_case.verification.findings[:4]
                ]
                facts += ["conflicting_or_missing_receipts_require_review"]
        elif role == "dependency":
            result["identities"] = [
                dict(
                    ndc=i["canonical_ndc"],
                    classification=i["classification"],
                    review_required=i["review_required"],
                )
                for i in self.view["readiness"]["identities"]
            ]
            result["historical_custody"] = "unproven"
            facts = [
                "hospital_identifiers_simulated",
                "product_identity_deterministically_checked",
                "historical_version_custody_unproven",
            ]
        elif role == "trade":
            raw = [v for v in raw if v.get("meaning") == "trade_aggregate"]
            if raw:
                raise ValueError("TRADE_SCOPE_REQUIRES_SEPARATE_REVIEW")
            result["admitted_trade_rows"] = 0
            facts = [
                "comparable_trade_evidence_unavailable",
                "operational_shipments_are_not_trade_aggregates",
            ]
        elif role == "hospital":
            result["forecasts"] = [
                {k: f[k] for k in ("hospital_id", "initial_usable", "total_unmet", "analysis_id")}
                for f in self.view["forecasts"]
            ]
            result["scope"] = "Inventory, demand and lot availability only; no clinical claims"
            facts = ["hospital_stock_and_demand_simulated", "unconfirmed_orders_excluded"] + [
                "unmet:" + f["hospital_id"] + ":" + str(f["total_unmet"]) for f in self.view["forecasts"]
            ]
        elif role == "clinical":
            result["saline_patient_outcome_model_supported"] = False
            facts = ["saline_clinical_outcomes_unsupported", "oncology_model_not_applicable_to_saline"]
        elif role == "memory":
            finding_values = [
                Finding(
                    claim_key="final-" + p["role"],
                    value=json.dumps(p),
                    kind="derived",
                    evidence_ids=p["evidence_ids"],
                    source_ids=["tool-validated-reconstructed-replay"],
                    limitations=["Fictional hospital operations; historical public source custody unproven"],
                )
                for p in self.predecessors()
            ]
            raw = remember(
                self.root / "hmem",
                "final-native",
                self.case.dataset_version,
                self.case.checkpoint.cutoff,
                self.case.candidates,
                self.case.selected,
                node=self.service.node,
                additional_findings=finding_values,
                query_text="rejected candidate donor shortage infeasible failures unconfirmed supplier human review",
            )
            self.memory = raw
            (self.root / "hmem.json").write_text(json.dumps(raw, indent=2) + "\n")
            facts = ["actual_hmem_findings_persisted", "actual_hmem_context_recalled"]
        else:
            result["candidates"] = [
                {
                    k: c.get(k)
                    for k in ("candidate_id", "source_id", "hospital_id", "quantity", "feasible", "failures")
                }
                for c in self.case.candidates
            ]
            result["drafts"] = [
                {k: a[k] for k in ("action_id", "candidate_id", "status", "dispatch_available")}
                for a in self.case.actions
            ]
            facts = [
                "deterministic_candidate_constraints_retained",
                "human_pharmacy_review_required",
                "no_physical_dispatch_authority",
            ]
            if role == "mitigation":
                if args.candidate_id not in {c["candidate_id"] for c in self.case.candidates}:
                    raise ValueError("CANDIDATE_LOOKUP_OUTSIDE_CASE")
                result["investigated_candidate"] = args.candidate_id
                self.trace.event(
                    "mitigation.candidate_inspection", agent_id=role, selected_for_inspection=args.candidate_id
                )
            if role == "feasibility":
                result["planner_proposal"] = self.completed.get("mitigation", {}).get("candidate_id")
                self.trace.event(
                    "planner.critic_feedback",
                    agent_id=role,
                    proposal=result["planner_proposal"],
                    rejection_codes=sorted({f for c in self.case.candidates for f in c["failures"]}),
                )
            if role == "actions":
                facts += ["existing_action_drafts_are_review_only"]
        recalled = self.memory.get("result", {}).get("memories", [])
        result["memory_ids"] = [m["memory"]["id"] for m in recalled]
        result["recalled_findings"] = []
        rejected = []
        for m in recalled:
            finding = json.loads(m["memory"]["content"])
            value = json.loads(finding["value"])
            result["recalled_findings"].append(
                dict(
                    memory_id=m["memory"]["id"],
                    claim_key=finding["claim_key"],
                    evidence_ids=finding["evidence_ids"],
                    value=value,
                )
            )
            if isinstance(value, dict) and value.get("feasible") is False:
                rejected.append(value["candidate_id"])
        result["excluded_due_to_recalled_rejection"] = rejected
        # Keep actual memory facts bounded on the wire; complete output is retained.
        for m in result["recalled_findings"]:
            v = m["value"]
            if isinstance(v, dict):
                m["value"] = {
                    k: v[k] for k in ("candidate_id", "feasible", "failures", "observation") if k in v
                }
        if role in {"memory", "mitigation", "feasibility", "coordinator"}:
            self.trace.event(
                "hmem.influence",
                agent_id=role,
                memory_ids=result["memory_ids"],
                rejected_candidate_ids=rejected,
                policy="Recalled rejections excluded from model recommendation; deterministic feasibility also required",
            )
        result.update(evidence_ids=evidence, fact_keys=facts)
        path = self.root / (role + "-tool.json")
        path.write_text(json.dumps(raw, sort_keys=True, default=str) + "\n")
        result.update(
            tool_result_reference=path.name, tool_result_sha256=sha256(path.read_bytes()).hexdigest()
        )
        return result


def run(args, client, budget, approval):
    proof = json.loads(args.zetaris_proof.read_text())
    if proof.get("mode") != "live-zetaris" or proof.get("status") != "passed":
        raise ValueError("ACTUAL_ZETARIS_PROOF_REQUIRED")
    query_records = json.loads((args.zetaris_proof.parent / "queries.json").read_text())
    joined = next(q["response"] for q in query_records if q["sql"] == proof["query"])
    if sha256(json.dumps(joined, sort_keys=True).encode()).hexdigest() != proof["result_sha256"]:
        raise ValueError("ZETARIS_PROOF_RESULT_CHANGED")
    service = OperatorService(args.store_root)
    case = service.helene_store.get(proof["investigation_id"])
    resuming = getattr(args, "resume", False)
    if (args.output_dir / "closed.json").exists():
        raise ValueError("RUN_CLOSED_NO_FURTHER_INFERENCE")
    if args.output_dir.exists() and not resuming:
        raise ValueError("PRESERVE_EXISTING_RUN_NO_AUTOMATIC_RETRY")
    args.output_dir.mkdir(parents=True, exist_ok=resuming)
    root = args.output_dir
    run_id = "final-" + uuid4().hex
    if not resuming:
        (root / "authorization.json").write_text(
            json.dumps(
                dict(
                    run_id=run_id,
                    new_authorization="Final backend user brief 2026-10-27",
                    limits=budget.summary(),
                    prior_run_preserved="full-agent-run/live",
                    no_commit=True,
                ),
                indent=2,
            )
            + "\n"
        )
    trace = ExecutionTrace()
    profiles = FinalProfiles(case, proof, service, root, trace)
    prompts = {
        r: "You are the existing PharmaX "
        + r
        + " specialist. "
        + RESPONSIBILITIES[r]
        + " For this Helene case use ONLY your offered scoped read and supported fact keys. "
        "Hospital role: inventory/demand only, never clinical conclusions. Historical custody is unproven. "
        "Hospital records are simulated. After the read submit agent_finding citing actual evidence and "
        "fact_keys. Acknowledge recalled memory_ids when supplied. Recommendations may select only "
        "a supplied feasible candidate not excluded by recalled rejection; all actions require human pharmacy review."
        for r in RESPONSIBILITIES
    }
    session = ModelSession(
        client, authorization=approval, max_requests=36, profiles=prompts, budget=budget, max_tokens=384
    )
    records = {}
    settled_failed = set()
    previous_events = []
    resume_dependencies = []
    if resuming:
        old = json.loads((root / "summary.json").read_text())
        if all(v != "not_attempted" for v in old["roles"].values()):
            raise ValueError("RUN_CLOSED_ALL_ROLES_ATTEMPTED")
        prior_trace = json.loads((root / "trace.json").read_text())
        if (
            old["investigation_id"] != case.investigation_id
            or old["budget"]["unmeasured_failed_tokens_reserved"]
        ):
            raise ValueError("RESUME_BASIS_OR_SPENT_USAGE_UNVERIFIED")
        run_id = old["run_id"]
        elapsed = (
            datetime.now(timezone.utc) - datetime.fromisoformat(old["budget"]["started_at_utc"])
        ).total_seconds()
        if elapsed >= 900:
            raise ServiceError("VERIFICATION_TIME_BUDGET_EXHAUSTED")
        budget.started = monotonic() - elapsed
        budget.deadline = budget.started + 900
        budget.started_at = old["budget"]["started_at_utc"]
        budget.turns = old["budget"]["logical_turns_reserved"]
        budget.attempts = old["budget"]["possible_upstream_attempts_reserved"]
        budget.reported_tokens = old["budget"]["reported_or_reserved_tokens"]
        session.calls = session.dispatches = old["actual_model_posts"]
        trace.graph = [ExecutionTask.model_validate(t) for t in prior_trace["graph"]]
        previous_events = prior_trace["events"]
        (root / ("segment-" + str(session.dispatches) + "-summary.json")).write_text(
            json.dumps(old, indent=2) + "\n"
        )
        (root / ("segment-" + str(session.dispatches) + "-trace.json")).write_text(
            json.dumps(prior_trace, indent=2) + "\n"
        )
        ids = set()
        for role in RESPONSIBILITIES:
            rec = json.loads((root / (role + ".json")).read_text())
            rs = rec.get("model_receipts", [])
            if not rs:
                continue
            if any(m["returned_model"] != MODEL or m["winning_route"]["provider"] != "nvidia" for m in rs):
                raise ValueError("RESUME_PROVIDER_MISMATCH")
            for m in rs:
                if m["response_id"] in ids:
                    raise ValueError("RESUME_DUPLICATE_RECEIPT")
                ids.add(m["response_id"])
            for t in rec["request_records"]:
                if t.get("http_status") != 200 or not t.get("usage_reported") or "completed_at" not in t:
                    raise ValueError("RESUME_TRANSPORT_USAGE_NOT_SETTLED")
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(t["completed_at"])).total_seconds()
                budget.tickets.append(
                    dict(
                        t,
                        settled=True,
                        start=monotonic() - t["elapsed_seconds"],
                        rate_until=monotonic() - age + 60,
                    )
                )
            if (
                sha256((root / rec["tool_result_reference"]).read_bytes()).hexdigest()
                != rec["tool_result_sha256"]
            ):
                raise ValueError("RESUME_TOOL_RESULT_CHANGED")
            actual = json.loads((root / (role + "-model-tool-result.json")).read_text())
            retained = next(
                e["data"]["result"]
                for e in previous_events
                if e["operation"] == "data.specialist_read" and e["data"].get("agent_id") == role
            )
            if actual != retained:
                raise ValueError("RESUME_DELIVERED_PROJECTION_CHANGED")
            records[role] = rec
            session.response_receipts.extend(rs)
            budget.roles[role] = session.by_role[role] = len(rs)
            try:
                answer = schema(role).model_validate(rs[-1]["arguments"])
                validate_finding(role, answer, actual)
            except ValueError:
                if rec["status"] != "failed" or rec.get("error_code") not in {
                    "FINDING_CITATION_OR_FACT_UNSUPPORTED",
                    "ROLE_FINDING_REQUIRES_DIRECT_SUPPORTED_FACT",
                    "CANDIDATE_UNSAFE_OR_NOT_SUPPORTED",
                }:
                    raise
                # Exhausted/unsupported role stays failed; allow independent closed
                # reads using deterministic calculations, never its rejected finding.
                settled_failed.add(role)
                failed = next(t for t in trace.graph if t.agent_id == role)
                checkpoint = trace.launch(
                    "coordinator", dependencies=[failed.task_id], read_only=True, require_success=False
                )
                trace.finish(checkpoint, "complete")
                trace.event(
                    "validation.failure_retained",
                    agent_id=role,
                    error_code=rec["error_code"],
                    policy="No repeated inference; rejected finding excluded from all downstream context",
                )
                resume_dependencies.append(checkpoint.task_id)
                continue
            if rec["status"] != "complete":
                if rec.get("error_code") not in {
                    "FINDING_CITATION_OR_FACT_UNSUPPORTED",
                    "CANDIDATE_UNSAFE_OR_NOT_SUPPORTED",
                }:
                    raise ValueError("RESUME_FAILURE_NOT_REVIEWABLE")
                (root / (role + "-original-validation.json")).write_text(json.dumps(rec, indent=2) + "\n")
                rec.update(
                    status="complete",
                    live_verified=True,
                    structured_finding=answer.model_dump(),
                    validation_review=(
                        "Unchanged native candidate reference is feasible, not excluded by recalled rejection and present in actual delivered review-only drafts; no action authority. Original rejection preserved; no model request repeated."
                        if role == "actions"
                        else "Unchanged native output citations and fact keys are explicitly supported by actual retained predecessor_findings; original rejection preserved. No model request repeated."
                    ),
                )
                rec.pop("error_code", None)
                (root / (role + ".json")).write_text(json.dumps(rec, indent=2) + "\n")
                failed = next(t for t in trace.graph if t.agent_id == role)
                review = trace.launch(
                    role, dependencies=[failed.task_id], read_only=True, require_success=False
                )
                trace.finish(review, "complete")
                resume_dependencies.append(review.task_id)
            profiles.completed[role] = answer.model_dump()
        if len(ids) != session.dispatches or len(budget.tickets) != budget.turns:
            raise ValueError("RESUME_SPENT_BUDGET_MISMATCH")
        if (root / "hmem.json").exists():
            profiles.memory = json.loads((root / "hmem.json").read_text())

    def save():
        summary = dict(
            run_id=run_id,
            investigation_id=case.investigation_id,
            status="complete" if len(profiles.completed) == 12 else "partial",
            genuinely_completed=len(profiles.completed),
            roles={r: records.get(r, {}).get("status", "not_attempted") for r in RESPONSIBILITIES},
            actual_model_posts=session.dispatches,
            actual_upstream_attempts=None,
            budget=budget.summary(),
            zetaris_trace_id=proof["trace_id"],
            model=MODEL,
            hmem_influence=bool(profiles.memory),
            application_retry_count=0,
            finalized_at=datetime.now(timezone.utc).isoformat(),
            limitations=[
                "Real current inference over reconstructed historical manufacturer documents and simulated hospital records",
                "No clinical saline outcome estimates or physical action authority",
                "Internal retry token usage is not exposed; no independent billing receipt",
            ],
        )
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (root / "trace.json").write_text(
            json.dumps(
                dict(
                    graph=[t.model_dump(mode="json") for t in trace.graph],
                    events=previous_events + [e.model_dump(mode="json") for e in trace.events],
                ),
                indent=2,
            )
            + "\n"
        )
        return summary

    def execute(role):
        record = dict(
            role_id=role,
            role_name=RESPONSIBILITIES[role],
            run_id=run_id,
            investigation_id=case.investigation_id,
            status="started",
            continuation_occurred=False,
            live_verified=False,
            predecessor_roles=[p["role"] for p in profiles.predecessors()],
            zetaris_trace_id=proof["trace_id"],
        )
        try:
            wire, canonical, _ = TOOLS[role]
            context = dict(
                objective=RESPONSIBILITIES[role],
                case_id=case.investigation_id,
                predecessors=profiles.predecessors()[-4:],
                allowed_conclusions=SCOPES[role],
            )
            if role == "mitigation":
                context["candidate_choices"] = [
                    dict(id=c["candidate_id"], quantity=c["quantity"]) for c in case.candidates
                ]
            _, arguments, call_id = session.select(
                role, {wire: CandidateInput if role == "mitigation" else Empty}, context, trace
            )
            result = profiles.tool(role, arguments)
            record.update(
                tool_selected=canonical,
                tool_input=arguments.model_dump(),
                tool_input_sha256=sha256(arguments.model_dump_json().encode()).hexdigest(),
                tool_result_reference=result["tool_result_reference"],
                tool_result_sha256=result["tool_result_sha256"],
                memory_ids=result["memory_ids"],
                excluded_due_to_recalled_rejection=result["excluded_due_to_recalled_rejection"],
            )
            (root / (role + "-model-tool-result.json")).write_text(json.dumps(result, indent=2) + "\n")
            session.tool_result(role, call_id, wire, arguments.model_dump(), result, trace)
            for continuation in range(2):
                _, answer, answer_call = session.select(
                    role,
                    {"agent_finding": schema(role, result)},
                    dict(
                        instruction="Return your role finding. Cite at least one DIRECT fact key below. Other facts/citations may come only from explicitly delivered predecessor findings. Acknowledge recalled memory. Select only feasible candidates.",
                        direct_fact_keys=result["fact_keys"],
                        direct_evidence_ids=result["evidence_ids"],
                        memory_ids=result["memory_ids"],
                    ),
                    trace,
                )
                record["continuation_occurred"] = True
                try:
                    validate_finding(role, answer, result)
                    break
                except ValueError as exc:
                    record.setdefault("validation_rejections", []).append(
                        dict(code=str(exc), actual_arguments=answer.model_dump())
                    )
                    if continuation:
                        raise
                    session.tool_result(
                        role,
                        answer_call,
                        "agent_finding",
                        answer.model_dump(),
                        dict(
                            accepted=False,
                            error_code=str(exc),
                            corrective_attempt_remaining=1,
                            instruction="Correct only unsupported citations, fact keys, memory acknowledgement or unsafe candidate; original tool evidence remains authoritative.",
                        ),
                        trace,
                    )
            with profiles.lock:
                profiles.completed[role] = answer.model_dump()
            record.update(status="complete", live_verified=True, structured_finding=answer.model_dump())
            trace.event("specialist.validated_finding", agent_id=role, **answer.model_dump())
            return RoleExecution(role=role, status="complete")
        except (ServiceError, ValueError, KeyError, TypeError) as exc:
            record.update(
                status="failed", error_code=exc.code if isinstance(exc, ServiceError) else str(exc)[:100]
            )
            return RoleExecution(role=role, status="failed", error_code=record["error_code"])
        finally:
            record["model_receipts"] = [r for r in session.response_receipts if r["role"] == role]
            record["request_records"] = [
                {k: v for k, v in t.items() if k not in {"start", "settled", "rate_until"}}
                for t in budget.tickets
                if t["role"] == role
            ]
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
            records[role] = record
            (root / (role + ".json")).write_text(json.dumps(record, indent=2) + "\n")

    try:
        if MODEL not in session.discover():
            raise ServiceError("NEMOTRON_NOT_DISCOVERED")
        session.ready = True
        dependencies = resume_dependencies
        for wave in WAVES:
            wave = tuple(r for r in wave if r not in profiles.completed and r not in settled_failed)
            if not wave:
                continue
            taskspec = [(r, lambda r=r: execute(r)) for r in wave]
            values, tasks = concurrent_reads(
                trace,
                taskspec,
                dependencies=dependencies,
                max_workers=2,
                timeout=max(0.1, min(120, budget.deadline - monotonic())),
                result_model=RoleExecution,
            )
            dependencies = [t.task_id for t in tasks]
            save()
            print(
                json.dumps(dict(wave=wave, statuses=[v.status for v in values], calls=session.dispatches)),
                flush=True,
            )
            if any(v.status != "complete" for v in values):
                break
    finally:
        for r in RESPONSIBILITIES:
            if r not in records:
                records[r] = dict(
                    role_id=r,
                    status="not_attempted",
                    live_verified=False,
                    error_code=session.failure or "DEPENDENCY_UNSATISFIED",
                )
                (root / (r + ".json")).write_text(json.dumps(records[r], indent=2) + "\n")
        summary = save()
        summary["further_inference_authorized"] = False
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        if all(r.get("continuation_occurred") for r in records.values()):
            (root / "closed.json").write_text(
                json.dumps(
                    dict(
                        run_id=run_id,
                        reason="All twelve roles attempted; per-role failures retained; no further inference authorized",
                        actual_posts=session.dispatches,
                    ),
                    indent=2,
                )
                + "\n"
            )
        from .native_status import publish

        publish(service, root, proof)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", required=True)
    p.add_argument("--runtime-token-file", type=Path, required=True)
    p.add_argument("--engine-root", type=Path, required=True)
    p.add_argument("--state-root", type=Path, required=True)
    p.add_argument("--zetaris-proof", type=Path, required=True)
    p.add_argument("--store-root", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument(
        "--resume",
        action="store_true",
        help="Same run only: retain original deadline and all consumed budgets; review only retained supported findings",
    )
    args = p.parse_args()
    os.environ.update(
        METERLESS_BASE_URL=args.base_url,
        METERLESS_MODEL=MODEL,
        METERLESS_RUNTIME_TOKEN=args.runtime_token_file.read_text().strip(),
    )
    budget = VerificationBudget(
        max_turns=36, max_attempts=108, token_cap=90000, seconds=900, per_role=3, rate_limit=12
    )
    approval = reviewed_authorization(args.base_url, args.engine_root, args.state_root, budget)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
        report = run(args, client, budget, approval)
    print(json.dumps(report, indent=2))
    return 0 if report["genuinely_completed"] == 12 else 1


if __name__ == "__main__":
    raise SystemExit(main())
