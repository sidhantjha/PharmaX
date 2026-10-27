import sqlite3
import pytest
from pydantic import ValidationError
from contracts.model_verification import ModelVerificationStatus
from phases.phase_05_product.service import OperatorService
from phases.phase_05_product.native_status import read_status, initialize
from phases.phase_06_helene.pipeline import replay_checkpoint


def test_absent_native_run_is_unavailable_and_ledger_is_immutable(tmp_path):
    service = OperatorService(tmp_path)
    case = replay_checkpoint("2024-10-01")
    service.helene_store.save(case)
    status = read_status(service, case.investigation_id)
    assert status.available is False and status.findings_validated == status.roles_executed == 0
    http = service.handle("GET", "/api/v1/helene/cases/" + case.investigation_id + "/model-verification")
    assert http[0] == 200 and http[1]["mode"] == "unavailable"
    initialize(service.store)
    with service.store.connect() as db:
        db.execute(
            "INSERT INTO helene_model_runs(case_id,run_id,payload) VALUES(?,?,?)",
            (case.investigation_id, "test", "{}"),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE helene_model_runs SET payload=?", ("{}",))
    with pytest.raises(ValidationError):
        ModelVerificationStatus(
            investigation_id=case.investigation_id,
            available=False,
            mode="unavailable",
            roles_executed=12,
            findings_validated=12,
        )
