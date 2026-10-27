"""Immutable case snapshots and append-only optimistic action revisions in local SQLite."""
from pathlib import Path
import json
import sqlite3
from contracts.operator import OperatorCase, ActionPackage, CaseSummary
from phases.phase_01_foundation.sources import digest
from .actions import review


class CaseStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, created TEXT, payload TEXT, hash TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS actions (case_id TEXT, id TEXT, revision INTEGER, payload TEXT, PRIMARY KEY(id,revision))')
            db.executescript('''CREATE TRIGGER IF NOT EXISTS immutable_cases_update BEFORE UPDATE ON cases
                BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_cases_delete BEFORE DELETE ON cases
                BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_actions_update BEFORE UPDATE ON actions
                BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_actions_delete BEFORE DELETE ON actions
                BEGIN SELECT RAISE(ABORT,'immutable'); END;''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, case):
        case = OperatorCase.model_validate(case)
        payload = case.model_dump_json()
        with self.connect() as db:
            db.execute('INSERT INTO cases VALUES (?,?,?,?)',
                       (case.investigation_id,case.created_at.isoformat(),payload,digest(case.model_dump(mode='json'))))
            for action in case.actions:
                db.execute('INSERT INTO actions VALUES (?,?,?,?)',
                           (case.investigation_id,action.action_id,action.revision,action.model_dump_json()))
        return case

    def get(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT payload,hash FROM cases WHERE id=?',(identity,)).fetchone()
        if row is None:
            raise KeyError('INVESTIGATION_NOT_FOUND')
        # Hash the retained representation before applying additive model defaults.
        # Older immutable snapshots legitimately omit Phase 5B fields.
        if digest(json.loads(row[0])) != row[1]:
            raise ValueError('SNAPSHOT_INTEGRITY_FAILED')
        return OperatorCase.model_validate_json(row[0])

    def snapshot_hash(self, identity):
        self.get(identity)  # Verify the retained representation before exposing its identity.
        with self.connect() as db:
            return db.execute('SELECT hash FROM cases WHERE id=?',(identity,)).fetchone()[0]

    def summaries(self, limit=20, offset=0):
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
            rows = db.execute('SELECT payload FROM cases ORDER BY created DESC,id LIMIT ? OFFSET ?', (limit,offset)).fetchall()
        return total, [CaseSummary(**OperatorCase.model_validate_json(r[0]).model_dump(include={
            'investigation_id','scenario','created_at','status','parent_id'})) for r in rows]

    def actions(self, identity):
        self.get(identity)
        with self.connect() as db:
            rows = db.execute('SELECT a.payload FROM actions a JOIN (SELECT id,MAX(revision) r FROM actions '
                'WHERE case_id=? GROUP BY id) b ON a.id=b.id AND a.revision=b.r ORDER BY a.id',(identity,)).fetchall()
        return [ActionPackage.model_validate_json(r[0]) for r in rows]

    def review(self, identity, action_id, request):
        self.get(identity)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM actions WHERE case_id=? AND id=? ORDER BY revision DESC LIMIT 1',
                             (identity,action_id)).fetchone()
            if row is None:
                raise KeyError('ACTION_NOT_FOUND')
            updated = review(ActionPackage.model_validate_json(row[0]),request)
            db.execute('INSERT INTO actions VALUES (?,?,?,?)',(identity,action_id,updated.revision,updated.model_dump_json()))
        return updated
