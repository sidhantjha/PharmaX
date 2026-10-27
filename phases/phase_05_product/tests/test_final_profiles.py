"""Offline canonical tools and explicit mock transport; never live receipt evidence."""

import json
from types import SimpleNamespace
import httpx
import pytest
from pydantic import ValidationError
from phases.phase_01_foundation.inference_policy import InferenceAuthorization
from phases.phase_05_product.execution import ExecutionTrace
from phases.phase_05_product.final_specialists import (
    FinalProfiles,
    TOOLS,
    SCOPES,
    schema,
    validate_finding,
    run,
)
from phases.phase_05_product.live_specialists import Empty
from phases.phase_05_product.service import OperatorService
from phases.phase_05_product.verification_budget import VerificationBudget
from phases.phase_05_product.verified_runtime import MODEL
from phases.phase_06_helene.pipeline import replay_checkpoint
from phases.phase_08_operations.ingestion import replay_importer
from phases.phase_05_product.tests.test_model_chat import response
from datetime import datetime, timedelta, timezone
from hashlib import sha256


def test_scoped_tools_recall_and_safety_without_network(tmp_path):
    case = replay_checkpoint("2024-10-01", importer=replay_importer)
    p = FinalProfiles(
        case,
        dict(
            trace_id="explicit-offline-test",
            result_sha256="0" * 64,
            source_lineage=dict(manufacturer=[s.model_dump(mode="json") for s in case.admission.sources]),
        ),
        OperatorService(tmp_path / "store"),
        tmp_path,
        ExecutionTrace(),
    )
    for role in TOOLS:
        args = (
            SimpleNamespace(candidate_id=case.candidates[0]["candidate_id"])
            if role == "mitigation"
            else Empty()
        )
        result = p.tool(role, args)
        feasible = [c["candidate_id"] for c in result.get("candidates", []) if c["feasible"]]
        answer = schema(role)(
            observation=SCOPES[role][0],
            evidence_ids=result["evidence_ids"],
            fact_keys=result["fact_keys"][:2],
            memory_ids=result["memory_ids"],
            candidate_id=feasible[0] if feasible and role in {"mitigation", "coordinator"} else None,
        )
        validate_finding(role, answer, result)
        p.completed[role] = answer.model_dump()
        if role == "hospital":
            with pytest.raises(ValidationError):
                schema(role)(**dict(answer.model_dump(), observation="clinical_model_unavailable"))
        if role == "mitigation":
            assert result["memory_ids"]
            assert result["excluded_due_to_recalled_rejection"]
            bad = next(c["candidate_id"] for c in result["candidates"] if not c["feasible"])
            with pytest.raises(ValueError, match="CANDIDATE_UNSAFE"):
                validate_finding(role, answer.model_copy(update={"candidate_id": bad}), result)
            with pytest.raises(ValueError, match="NOT_ACKNOWLEDGED"):
                validate_finding(role, answer.model_copy(update={"memory_ids": []}), result)


def test_cross_agent_citations_require_actual_delivered_predecessor_support():
    result = dict(
        evidence_ids=["direct"],
        fact_keys=["historical_version_custody_unproven"],
        memory_ids=[],
        predecessor_findings=[
            dict(evidence_ids=["manufacturer"], fact_keys=["manufacturer_provenance_retained"])
        ],
    )
    answer = schema("dependency")(
        observation="simulated_hospital_records",
        evidence_ids=["manufacturer"],
        fact_keys=["manufacturer_provenance_retained", "historical_version_custody_unproven"],
    )
    validate_finding("dependency", answer, result)
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        validate_finding("dependency", answer.model_copy(update={"evidence_ids": ["not-delivered"]}), result)


def test_actions_can_reference_only_a_feasible_existing_review_draft():
    result = dict(
        evidence_ids=["ops"],
        fact_keys=["human_pharmacy_review_required"],
        memory_ids=[],
        candidates=[dict(candidate_id="safe", feasible=True), dict(candidate_id="unsafe", feasible=False)],
        drafts=[dict(candidate_id="safe", dispatch_available=False)],
        excluded_due_to_recalled_rejection=[],
    )
    answer = schema("actions")(
        observation="draft_only",
        evidence_ids=["ops"],
        fact_keys=["human_pharmacy_review_required"],
        candidate_id="safe",
    )
    validate_finding("actions", answer, result)
    with pytest.raises(ValueError, match="UNSAFE"):
        validate_finding("actions", answer.model_copy(update={"candidate_id": "unsafe"}), result)
    result["drafts"] = []
    with pytest.raises(ValueError, match="NO_REVIEW_ONLY_DRAFT"):
        validate_finding("actions", answer, result)


def test_twelve_native_loops_explicit_mock_and_one_supported_correction(tmp_path, monkeypatch):
    base = "http://127.0.0.1:8088/v1"
    monkeypatch.setenv("METERLESS_BASE_URL", base)
    monkeypatch.setenv("METERLESS_MODEL", MODEL)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "EXPLICIT-MOCK-TOKEN")
    service = OperatorService(tmp_path / "store")
    case = replay_checkpoint("2024-10-01", importer=replay_importer)
    service.helene_store.save(case)
    joined = dict(headers=["explicit-mock-query"], data=[])
    proof = dict(
        mode="live-zetaris",
        status="passed",
        trace_id="explicit-mock-not-live",
        result_sha256=sha256(json.dumps(joined, sort_keys=True).encode()).hexdigest(),
        query="EXPLICIT_MOCK_QUERY",
        endpoint="http://explicit-mock",
        server={},
        namespace="explicit_mock",
        source_tables=["mock_a", "mock_b"],
        returned_rows=0,
        response_schema=[],
        investigation_id=case.investigation_id,
        source_lineage=dict(manufacturer=[s.model_dump(mode="json") for s in case.admission.sources]),
    )
    (tmp_path / "proof.json").write_text(json.dumps(proof))
    (tmp_path / "queries.json").write_text(json.dumps([dict(sql=proof["query"], response=joined)]))
    counts = {}

    def transport(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        body = json.loads(req.content)
        role = next(r for r in TOOLS if f"PharmaX {r} specialist" in body["messages"][0]["content"])
        counts[role] = counts.get(role, 0) + 1
        name = body["tools"][0]["function"]["name"]
        if name != "agent_finding":
            args = {"candidate_id": case.candidates[0]["candidate_id"]} if role == "mitigation" else {}
        else:
            result = next(json.loads(m["content"]) for m in body["messages"] if m["role"] == "tool")
            feasible = [c["candidate_id"] for c in result.get("candidates", []) if c["feasible"]]
            args = dict(
                observation=SCOPES[role][0],
                evidence_ids=result["evidence_ids"],
                fact_keys=result["fact_keys"][:2],
                candidate_id=feasible[0] if feasible and role in {"mitigation", "coordinator"} else None,
                memory_ids=result["memory_ids"],
            )
            if role == "hospital" and counts[role] == 2:
                # Schema-valid but semantically unsupported: exercises the one
                # corrective continuation rather than malformed-wire handling.
                args['candidate_id']='UNSUPPORTED-MOCK-CANDIDATE'
        value = response()
        value["choices"][0]["message"]["tool_calls"][0]["function"] = {
            "name": name,
            "arguments": json.dumps(args),
        }
        value["meterless"]["receipt_id"] = "explicit-mock-receipt-" + role + "-" + str(counts[role])
        value["id"] = "explicit-mock-" + role + "-" + str(counts[role])
        value["choices"][0]["message"]["tool_calls"][0]["id"] = "mock-" + role + "-" + str(counts[role])
        return httpx.Response(200, json=value)

    budget = VerificationBudget(max_turns=36, max_attempts=108, token_cap=90000, seconds=900, per_role=3)
    approval = InferenceAuthorization(
        base,
        MODEL,
        "nvidia",
        datetime.now(timezone.utc) + timedelta(minutes=20),
        ("explicit mocked test only",),
        True,
        False,
        True,
        max_upstream_attempts_per_request=3,
        runtime_guard=lambda: None,
        reserve_attempts=budget.reserve_attempts,
    )
    args = SimpleNamespace(
        zetaris_proof=tmp_path / "proof.json", store_root=tmp_path / "store", output_dir=tmp_path / "mock-run"
    )
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        report = run(args, client, budget, approval)
    assert report["genuinely_completed"] == 12
    assert report["actual_model_posts"] == 25 and budget.attempts == 75
    assert counts["hospital"] == 3 and all(v == 2 for r, v in counts.items() if r != "hospital")
