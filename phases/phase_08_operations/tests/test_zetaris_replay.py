"""Offline admission of mocked query rows; no claimed live query in unit tests."""

import pytest
from phases.phase_06_helene.pipeline import OPERATIONS, replay_checkpoint
from phases.phase_08_operations.ingestion import REQUIRED, replay_importer
from phases.phase_08_operations.zetaris_replay import materialize, encode, execute


def test_remote_rows_preserve_bytes_and_reject_incomplete_or_wrong_product(tmp_path):
    case = replay_checkpoint("2024-10-01", importer=replay_importer)
    rows = []
    for source in case.admission.sources:
        for feed in [*REQUIRED, "clocks"]:
            name = feed + (".json" if feed == "clocks" else ".csv")
            rows.append(
                [
                    source.evidence.evidence_id,
                    case.operations.products[0].product_id,
                    encode(source.model_dump(mode="json")),
                    encode([a.model_dump(mode="json") for a in case.admission.allocations]),
                    feed,
                    (OPERATIONS / "2024-10-01" / name).read_bytes().hex(),
                ]
            )
    lineage = materialize(rows, tmp_path / "ok", case)
    assert len(lineage["feed_hashes"]) == 10
    assert (tmp_path / "ok/inventory.csv").read_bytes() == (
        OPERATIONS / "2024-10-01/inventory.csv"
    ).read_bytes()
    with pytest.raises(ValueError, match="COVERAGE_INCOMPLETE"):
        materialize([r for r in rows if r[4] != "inventory"], tmp_path / "missing", case)
    wrong = [r[:] for r in rows]
    wrong[0][1] = "unmatched-product"
    with pytest.raises(ValueError, match="PRODUCT_MISMATCH"):
        materialize(wrong, tmp_path / "wrong", case)


def test_explicit_offline_mode_never_constructs_a_network_client(tmp_path, monkeypatch):
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("NETWORK_NOT_ALLOWED_IN_OFFLINE_MODE")

    monkeypatch.setattr(httpx, "Client", forbidden)
    case, proof = execute(tmp_path / "evidence", tmp_path / "store", mode="offline")
    assert proof["mode"] == "offline-sample" and proof["network_requests"] == 0
    assert proof["investigation_id"] == case.investigation_id
