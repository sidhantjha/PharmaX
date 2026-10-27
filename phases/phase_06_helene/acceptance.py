"""Real loopback HTTP checks plus independently reimported raw-input mutation proof."""

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

import httpx

from contracts.replay import HeleneCase
from phases.phase_05_product.service import OperatorService
from .fixtures import DAYS, write_csv
from .pipeline import OPERATIONS, replay_checkpoint
from .sources import load_sources


def mutation_proof(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    folder = Path(root) / "operations"
    shutil.copytree(OPERATIONS, folder)
    sources = load_sources()
    before = replay_checkpoint(DAYS[1], operations=folder, sources=sources)
    path = folder / DAYS[1] / "inventory.csv"
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[0]["on_hand"] = str(int(rows[0]["on_hand"]) + 1000)
    write_csv(path, rows)
    after = replay_checkpoint(DAYS[1], operations=folder, sources=sources, parent=before)
    assert before.forecasts[0].total_unmet > after.forecasts[0].total_unmet == 0
    assert before.selected != after.selected
    store = OperatorService(root / "store").helene_store
    store.save(before)
    store.save(after)
    old = next(r for r in before.admission.imports if r.feed == "inventory")
    new = next(r for r in after.admission.imports if r.feed == "inventory")
    return dict(
        changed_raw_file=str(path),
        before_sha256=old.content_sha256,
        after_sha256=new.content_sha256,
        parent_id=before.investigation_id,
        child_id=after.investigation_id,
        previous_unmet=before.forecasts[0].total_unmet,
        revised_unmet=after.forecasts[0].total_unmet,
        previous_selected=before.selected,
        revised_selected=after.selected,
        child_parent_hash=after.checkpoint.parent_sha256,
        persisted_parent_unchanged=store.get(before.investigation_id) == before,
    )


def run(base, output, frontend_fixture=None):
    if urlsplit(base).hostname not in ("127.0.0.1", "localhost") or urlsplit(base).scheme != "http":
        raise ValueError("LOOPBACK_ONLY_ACCEPTANCE")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    endpoints = []
    cases = {}
    with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:

        def read(path):
            r = client.get(path)
            r.raise_for_status()
            endpoints.append(dict(path=path, status=r.status_code))
            return r.json()

        read("/api/v1/health")
        listing = read("/api/v1/helene/cases")
        assert len(listing["items"]) == 4
        for summary in listing["items"]:
            value = read("/api/v1/helene/cases/" + summary["investigation_id"])
            c = HeleneCase.model_validate(value)
            cases[c.investigation_id] = value
            day = c.admission.sources[-1].issue_date.isoformat()
            assert day in DAYS and all(str(s.issue_date) <= day for s in c.admission.sources)
            assert len(c.forecasts) == len(c.operations.hospitals) == 3 and c.memory["writes"]
            if c.parent_id:
                assert c.memory["prior_scope"] and c.memory["prior_rejections"]
            for ev in c.evidence:
                if ev.raw_ref:
                    assert sha256(Path(ev.raw_ref).read_bytes()).hexdigest() == ev.content_sha256
            for f in c.forecasts + c.mitigated:
                for p in f.points:
                    assert (
                        p.closing_physical == p.opening_physical - p.expired_physical + p.received - p.served
                    )
            assert all(a["dispatch_available"] is False for a in c.actions)
        red = read("/api/v1/investigations")
        assert red["total"] >= 2
        r = client.post("/api/v1/helene/replay", json=dict(checkpoint=DAYS[0], strict=True))
        assert r.status_code == 422
        endpoints.append(dict(path="/api/v1/helene/replay (strict rejected)", status=r.status_code))
    proof = mutation_proof(output / "mutation")
    receipt = dict(
        classification="real loopback HTTP, authentic cached PDFs, simulated operations, actual FEFO/H-MEM",
        base_url=base,
        endpoints=endpoints,
        checkpoints=[
            dict(
                id=c["investigation_id"],
                source_count=len(c["admission"]["sources"]),
                before={f["hospital_id"]: f["total_unmet"] for f in c["forecasts"]},
                after={f["hospital_id"]: f["total_unmet"] for f in c["mitigated"]},
                memory_scope=c["memory"]["scope"],
            )
            for c in cases.values()
        ],
        mutation=proof,
        zetaris_queries=0,
        live_inference_requests=0,
        physical_actions=0,
    )
    (output / "acceptance.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if frontend_fixture:
        Path(frontend_fixture).write_text(json.dumps(dict(listing=listing, cases=cases), indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return receipt


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8876")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--frontend-fixture", type=Path)
    a = p.parse_args()
    run(a.base_url, a.output_dir, a.frontend_fixture)
