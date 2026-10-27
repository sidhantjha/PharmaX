"""Existing specialist roles, closed read tools and the existing H-MEM Node bridge."""

import json
import os
from pathlib import Path
import subprocess

from phases.phase_01_foundation.sources import digest
from phases.phase_04_agents.models import Finding


class ReplayTools:
    """No provider/network access; selectable read tools operate on the admitted saved case."""

    ALLOWED = {
        "verification": ("manufacturer.read",),
        "dependency": ("catalog.read", "orders.read"),
        "hospital": ("inventory.read", "consumption.read", "forecast.read"),
        "mitigation": ("mitigation.read",),
    }

    def __init__(self, case):
        self.case = case
        self.calls = 0
        self.receipts = []

    def call(self, role, tool):
        if self.calls >= 12 or tool not in self.ALLOWED.get(role, ()):
            raise ValueError("REPLAY_TOOL_BUDGET_OR_ROLE_DENIED")
        self.calls += 1  # Reserve before reading/serializing.
        values = {
            "manufacturer.read": lambda: self.case.admission.sources,
            "catalog.read": lambda: self.case.operations.products,
            "orders.read": lambda: self.case.operations.purchase_orders,
            "inventory.read": lambda: self.case.operations.inventory,
            "consumption.read": lambda: self.case.operations.consumption,
            "forecast.read": lambda: self.case.forecasts,
            "mitigation.read": lambda: self.case.candidates,
        }[tool]()
        result = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in values]
        if len(json.dumps(result)) > 150000:
            raise ValueError("REPLAY_TOOL_RESPONSE_BUDGET")
        receipt = dict(
            agent_id=role,
            tool=tool,
            result=result,
            execution="mocked-model-directed-real-tool",
            classification="executed-operation",
            dataset_version=self.case.dataset_version,
        )
        self.receipts.append(receipt)
        return result

    def mocked_investigation(self, selector):
        """Injected/mock model selects closed tools; real results feed its next selection."""
        history = []
        for role in self.ALLOWED:
            for _ in range(3):
                selection = selector(role, list(self.ALLOWED[role]), history)
                if selection is None:
                    break
                history.append(dict(role=role, tool=selection, result=self.call(role, selection)))
        return self.receipts


def remember(root, day, version, cutoff, candidates, selected, *, node=None, prior_scope=None,
             additional_findings=None, query_text="rejected donor shortage unconfirmed alternative supply"):
    """Actual bounded reference execution; scope prevents future/cross-scenario retrieval.

    Findings are derived decisions over the already-admitted fictional operations.
    Original document evidence stays in the case with its actual retrieval clock.
    """
    scope = digest(dict(scenario="helene", checkpoint=day, dataset=version, cutoff=cutoff.isoformat()))
    directory = Path(root) / scope
    bridge = Path(__file__).parents[1] / "phase_04_agents/bridge.mjs"
    findings = [
        Finding(
            claim_key="helene-rejected-" + str(i),
            value=json.dumps(c),
            kind="derived",
            evidence_ids=["replay-dataset-" + version],
            source_ids=["admitted-fictional-operations"],
            analysis_ids=c.get("analysis_ids", []),
            limitations=["Reconstructed fictional decision, not observed hospital history."],
        )
        for i, c in enumerate(candidates)
        if not c["feasible"]
    ][:12]
    findings += [
        Finding(
            claim_key="helene-selection",
            value=json.dumps(selected),
            kind="derived",
            evidence_ids=["replay-dataset-" + version],
            source_ids=["admitted-fictional-operations"],
            limitations=["Human review required; deterministic calculation."],
        )
    ]
    extra = [Finding.model_validate(f) for f in (additional_findings or [])]
    if len(extra) > 12:
        raise ValueError("HMEM_FINDING_BUDGET")
    findings += extra
    payload = dict(
        persist_dir=str(directory),
        scope=scope,
        findings=[f.model_dump(mode="json") for f in findings],
        query=query_text,
        clock=int(cutoff.timestamp() * 1000),
    )
    executable = node or os.environ.get("PHARMAX_NODE", "node")
    if len(json.dumps(payload).encode()) > 150000:
        raise ValueError("HMEM_REQUEST_BUDGET")
    proc = subprocess.run(
        [executable, "--experimental-transform-types", "--disable-warning=ExperimentalWarning", str(bridge)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if proc.returncode or len(proc.stdout) > 200000:
        raise ValueError("HMEM_REPLAY_FAILED")
    result = json.loads(proc.stdout)
    if not result.get("writes"):
        raise ValueError("HMEM_REPLAY_EMPTY")
    result["scope"] = scope
    result["classification"] = "actual H-MEM over derived simulated decisions; no inference"
    if prior_scope:
        if len(prior_scope) != 64 or any(c not in "0123456789abcdef" for c in prior_scope):
            raise ValueError("INVALID_PRIOR_MEMORY_SCOPE")
        prior_payload = dict(
            payload, persist_dir=str(Path(root) / prior_scope), scope=prior_scope, findings=[]
        )
        recalled = subprocess.run(
            [
                executable,
                "--experimental-transform-types",
                "--disable-warning=ExperimentalWarning",
                str(bridge),
            ],
            input=json.dumps(prior_payload),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if recalled.returncode or len(recalled.stdout) > 200000:
            raise ValueError("HMEM_PRIOR_RECALL_FAILED")
        result["prior_rejections"] = json.loads(recalled.stdout)["result"]
        result["prior_scope"] = prior_scope
    return result
