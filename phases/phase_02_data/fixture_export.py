"""Regenerate entirely simulated frontend examples from pinned inputs and scenario."""

import json
from pathlib import Path
from contracts import DataQuery
from .pipeline import assemble
from .store import local_query
from .validation import COLLECTIONS


def main():
    root = Path(__file__).resolve().parent
    raw = json.loads((root / "fixtures/source_inputs.json").read_text())
    snapshots = {k: (v["id"], v["receipt"], v["body"].encode()) for k, v in raw.items()}
    spec = json.loads((root / "fixtures/curated_event.json").read_text())
    bundle = assemble(snapshots, spec, fixture=True)
    (root / "fixtures/bundle.json").write_text(bundle.model_dump_json(indent=2) + "\n")
    for collection in COLLECTIONS:
        page = local_query(bundle, DataQuery(collection=collection, limit=100), mode="fixture")
        (root.parents[1] / "contracts/examples" / f"data-{collection}.json").write_text(
            page.model_dump_json(indent=2) + "\n"
        )
    print(f"Exported 13 frontend examples; all simulated; version={bundle.dataset_version}")


if __name__ == "__main__":
    main()
