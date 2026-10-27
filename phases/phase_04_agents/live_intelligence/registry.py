"""Append-only local semantic revisions, retrievals, durable freshness and work claims."""
from datetime import timezone
import json
from pathlib import Path
import sqlite3
from contracts import DisruptionSignal, SourceStatus


def iso(now):
    if now.tzinfo is None:
        raise ValueError('Aware cutoff required')
    return now.astimezone(timezone.utc).isoformat()


class Registry:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS revisions(revision TEXT PRIMARY KEY, event TEXT NOT NULL,
                    retrieved TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS retrievals(receipt TEXT PRIMARY KEY, source TEXT NOT NULL, checked TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS status(source TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(job TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT);
                CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def ingest(self, signals, *, receipts=(), source=None, checked=None):
        new = []
        with self.connect() as db:
            for signal in signals:
                signal = validate_signal(signal)
                inserted = db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)', (
                    signal.revision_id, signal.event_id, iso(signal.retrieved_at), signal.model_dump_json()))
                if inserted.rowcount:
                    new.append(signal)
            for receipt in receipts:
                db.execute('INSERT OR IGNORE INTO retrievals VALUES(?,?,?)', (receipt, source, iso(checked)))
        return new

    def latest(self, cutoff, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Signal limit must be 1..100')
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM revisions WHERE retrieved<=? ORDER BY retrieved DESC, revision DESC', (iso(cutoff),)).fetchall()
        # Keep the most recent eligible revision per logical event, never a future revision.
        selected = {}
        for row in rows:
            value = DisruptionSignal.model_validate_json(row[0])
            selected.setdefault(value.event_id, value)
        return list(selected.values())[:limit]

    def revisions(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM revisions ORDER BY event,retrieved,revision').fetchall()
        if len(rows) > 100:
            raise ValueError('Pilot publication limit: at most 100 event revisions')
        return [DisruptionSignal.model_validate_json(r[0]) for r in rows]

    def statuses(self):
        with self.connect() as db:
            values = db.execute('SELECT payload FROM status ORDER BY source').fetchall()
        return [SourceStatus.model_validate_json(r[0]) for r in values]

    def set_status(self, value):
        value = SourceStatus.model_validate(value)
        with self.connect() as db:
            db.execute('INSERT INTO status VALUES(?,?) ON CONFLICT(source) DO UPDATE SET payload=excluded.payload',
                       (value.source, value.model_dump_json()))

    def queue(self, key):
        with self.connect() as db:
            return bool(db.execute("INSERT OR IGNORE INTO jobs VALUES(?,'pending',NULL)", (key,)).rowcount)

    def claim(self, key, *, retry_failed=False):
        with self.connect() as db:
            states = ('pending', 'failed') if retry_failed else ('pending',)
            placeholders = ','.join('?' for _ in states)
            return bool(db.execute(f"UPDATE jobs SET state='running' WHERE job=? AND state IN ({placeholders})", (key,*states)).rowcount)

    def finish(self, key, run):
        data = run.model_dump_json()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO runs VALUES(?,?)', (run.run_id, data))
            db.execute('UPDATE jobs SET state=?,payload=? WHERE job=?',
                       ('complete' if run.status == 'complete' else 'failed', data, key))

    def runs(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM runs ORDER BY rowid DESC LIMIT 20').fetchall()]


def lifecycle(signal, cutoff, available_signals=()):
    if signal.retrieved_at > cutoff or (signal.effective_on and signal.effective_on > cutoff.date()):
        return 'not-yet-available'
    if signal.status == 'cancelled':
        return 'cancelled'
    if any(signal.event_id in newer.supersedes and newer.retrieved_at <= cutoff
           and (not newer.effective_on or newer.effective_on <= cutoff.date()) for newer in available_signals):
        return 'superseded'
    if signal.expires_on and signal.expires_on < cutoff.date():
        return 'expired'
    return signal.status


def investigation_key(signal, dependency, delays=(0,3,7)):
    from phases.phase_01_foundation.sources import digest
    return digest({'signal':signal.revision_id,'dataset':dependency.dataset_version,'delays':sorted(delays)})


def validate_signal(value):
    from phases.phase_01_foundation.sources import digest
    signal=DisruptionSignal.model_validate(value)
    fields=signal.model_dump(mode='json',exclude={'revision_id','retrieved_at','evidence','evidence_ids','indexed_at'})
    if digest(fields)!=signal.revision_id:
        raise ValueError('Signal revision hash mismatch')
    return signal
