"""Versioned Helene API on the existing operator connection and server."""

import json
import sqlite3
from datetime import datetime, timezone

from pydantic import ValidationError

from contracts.replay import HeleneCase
from phases.phase_01_foundation.sources import digest
from .pipeline import replay_checkpoint


class HeleneStore:
    def __init__(self, case_store):
        self.base = case_store
        with self.base.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS helene_cases (id TEXT PRIMARY KEY, payload TEXT, hash TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS helene_reviews (id TEXT, revision INTEGER, payload TEXT, PRIMARY KEY(id,revision))"
            )
            for table in ("helene_cases", "helene_reviews"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} "
                        "BEGIN SELECT RAISE(ABORT,'immutable'); END"
                    )

    def save(self, case):
        case = HeleneCase.model_validate(case)
        if case.parent_id:
            parent = self.get(case.parent_id)
            if case.checkpoint.parent_sha256 != digest(parent.model_dump(mode="json")):
                raise ValueError("HELENE_PARENT_HASH_MISMATCH")
        registry = {e.evidence_id for e in case.evidence}
        if case.verification:
            for claim in case.verification.claims:
                retained = next((e for e in case.evidence if e.evidence_id == claim.evidence.evidence_id), None)
                if retained != claim.evidence:
                    raise ValueError("HELENE_VERIFICATION_LINEAGE_MISSING")
        if any(p not in registry for e in case.evidence for p in e.parent_ids):
            raise ValueError("HELENE_EVIDENCE_PARENT_MISSING")
        if any(e not in registry for f in case.forecasts + case.mitigated for e in f.evidence_ids):
            raise ValueError("HELENE_CALCULATION_LINEAGE_MISSING")
        with self.base.connect() as db:
            db.execute(
                "INSERT INTO helene_cases VALUES (?,?,?)",
                (case.investigation_id, case.model_dump_json(), digest(case.model_dump(mode="json"))),
            )
            for action in case.actions:
                db.execute(
                    "INSERT INTO helene_reviews VALUES (?,?,?)", (action["action_id"], 1, json.dumps(action))
                )
        return case

    def get(self, identity):
        with self.base.connect() as db:
            row = db.execute("SELECT payload,hash FROM helene_cases WHERE id=?", (identity,)).fetchone()
        if not row:
            raise KeyError("HELENE_CASE_NOT_FOUND")
        if digest(json.loads(row[0])) != row[1]:
            raise ValueError("HELENE_SNAPSHOT_INTEGRITY_FAILED")
        return HeleneCase.model_validate_json(row[0])

    def cases(self):
        with self.base.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM helene_cases ORDER BY id")]
        return [self.get(i) for i in ids]

    def actions(self, case):
        results = []
        with self.base.connect() as db:
            for action in case.actions:
                row = db.execute(
                    "SELECT payload FROM helene_reviews WHERE id=? ORDER BY revision DESC LIMIT 1",
                    (action["action_id"],),
                ).fetchone()
                results.append(json.loads(row[0]))
        return results

    def review(self, case, identity, body):
        if not isinstance(body, dict) or set(body) != {"expected_revision", "status", "reviewer", "attested"}:
            raise ValueError("REVIEW_SCHEMA_INVALID")
        if not any(a["action_id"] == identity for a in case.actions):
            raise KeyError("ACTION_NOT_FOUND")
        with self.base.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = json.loads(
                db.execute(
                    "SELECT payload FROM helene_reviews WHERE id=? ORDER BY revision DESC LIMIT 1",
                    (identity,),
                ).fetchone()[0]
            )
            if type(body["expected_revision"]) is not int or current["revision"] != body["expected_revision"]:
                raise ValueError("REVISION_CONFLICT")
            if (
                body["status"]
                not in {"DRAFT": ["READY_FOR_REVIEW"], "READY_FOR_REVIEW": ["APPROVED"]}.get(
                    current["status"], []
                )
                or body["attested"] is not True
                or not isinstance(body["reviewer"], str)
                or not body["reviewer"].strip()
            ):
                raise ValueError("REVIEW_ATTESTATIONS_OR_TRANSITION_INVALID")
            current.update(
                status=body["status"],
                revision=current["revision"] + 1,
                reviewer=body["reviewer"],
                reviewed_at=datetime.now(timezone.utc).isoformat(),
            )
            db.execute(
                "INSERT INTO helene_reviews VALUES (?,?,?)",
                (identity, current["revision"], json.dumps(current)),
            )
        return current


def handle(service, method, path, body, query):
    if '/operations' in path:
        from phases.phase_08_operations.service import handle as operations_handle
        return operations_handle(service, method, path, body, query)
    store = service.helene_store
    parts = path.removeprefix("/api/v1/helene/").strip("/").split("/")
    try:
        if query:
            raise ValueError("HELENE_QUERY_NOT_SUPPORTED")
        if method == 'POST' and len(parts) == 3 and parts[0] == 'cases' and parts[2] == 'chat':
            from contracts.helene_chat import HeleneChatRequest
            from .chat import answer
            request = HeleneChatRequest.model_validate(body)
            if request.mode != 'offline':
                return 403, dict(schema_version='helene-chat-1', error='CHAT_LIVE_AUTHORIZATION_REQUIRED')
            return 200, answer(service, parts[1], request).model_dump(mode='json')
        if method == 'GET' and len(parts) == 4 and parts[0] == 'cases' and parts[2] == 'chat-sources':
            from .chat import source
            case = store.get(parts[1])
            return 200, dict(schema_version='helene-chat-source-1', investigation_id=case.investigation_id,
                             reference=parts[3], content=source(service, case, parts[3]))
        if method == 'GET' and len(parts) == 4 and parts[0] == 'cases' and parts[2] == 'chat':
            from .chat import history
            return 200, history(service, parts[1], parts[3]).model_dump(mode='json')
        if method == "GET" and parts == ["cases"]:
            cases = store.cases()
            return 200, dict(
                schema_version="6.1",
                items=[
                    dict(
                        investigation_id=c.investigation_id,
                        checkpoint=c.checkpoint.model_dump(mode="json"),
                        parent_id=c.parent_id,
                        reconciliation_scenario=c.verification.scenario if c.verification else None,
                    )
                    for c in cases
                ],
            )
        if method == "GET" and len(parts) == 2 and parts[0] == "cases":
            case = store.get(parts[1])
            result = case.model_dump(mode="json")
            result["actions"] = store.actions(case)
            return 200, result
        if method == 'GET' and len(parts)==3 and parts[0]=='cases' and parts[2]=='model-verification':
            from phases.phase_05_product.native_status import read_status
            return 200,read_status(service,parts[1]).model_dump(mode='json')
        if method == "GET" and len(parts) == 3 and parts[0] == "cases" and parts[2] == "verification":
            from phases.phase_07_reconciliation.pipeline import verify_case
            case = store.get(parts[1])
            verification = case.verification or verify_case(case)
            return 200, verification.model_dump(mode="json")
        if method == "POST" and parts == ["reconciliation"]:
            from phases.phase_07_reconciliation.pipeline import reconcile_scenario
            if not isinstance(body, dict) or set(body) - {"scenario", "parent_id"} or body.get("scenario") not in {"A", "B", "C"}:
                raise ValueError("RECONCILIATION_SCHEMA_INVALID")
            parent = store.get(body["parent_id"]) if body.get("parent_id") else None
            if not service.execution_lock.acquire(blocking=False):
                return 409, dict(schema_version="7.1", error="INVESTIGATION_BUSY")
            try:
                case = reconcile_scenario(body["scenario"], parent=parent,
                    memory_root=service.root / "helene-memory", node=service.node)
                try:
                    existing = store.get(case.investigation_id)
                except KeyError:
                    existing = None
                if existing is not None:
                    return 200, existing.model_dump(mode="json")
                store.save(case)
            finally:
                service.execution_lock.release()
            return 201, case.model_dump(mode="json")
        if method == "POST" and parts == ["replay"]:
            if not isinstance(body, dict) or set(body) - {"checkpoint", "parent_id", "strict"}:
                raise ValueError("REPLAY_SCHEMA_INVALID")
            parent = store.get(body["parent_id"]) if body.get("parent_id") else None
            if type(body.get("strict", False)) is not bool:
                raise ValueError("REPLAY_SCHEMA_INVALID")
            if not service.execution_lock.acquire(blocking=False):
                return 409, dict(schema_version="6.1", error="INVESTIGATION_BUSY")
            try:
                case = replay_checkpoint(
                    body["checkpoint"],
                    parent=parent,
                    strict=body.get("strict", False),
                    memory_root=service.root / "helene-memory",
                    node=service.node,
                )
                store.save(case)
            finally:
                service.execution_lock.release()
            return 201, case.model_dump(mode="json")
        if (
            method == "POST"
            and len(parts) == 5
            and parts[0] == "cases"
            and parts[2] == "actions"
            and parts[4] == "review"
        ):
            return 200, store.review(store.get(parts[1]), parts[3], body)
        return 404, dict(schema_version="6.1", error="HELENE_ENDPOINT_NOT_FOUND")
    except KeyError as exc:
        return 404, dict(schema_version="6.1", error=str(exc.args[0]))
    except sqlite3.IntegrityError:
        return 409, dict(schema_version="6.1", error="HELENE_REVISION_EXISTS")
    except (ValidationError, ValueError) as exc:
        code = str(exc)
        return (409 if code in ("REVISION_CONFLICT", "CHAT_CONVERSATION_CASE_MISMATCH", "CHAT_CONVERSATION_CHANGED") else 422), dict(
            schema_version="6.1",
            error=code if code.isupper() and len(code) < 100 else "HELENE_ADMISSION_INVALID",
        )
