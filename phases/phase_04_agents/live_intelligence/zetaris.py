"""Immutable signal revisions in the existing PharmaX Zetaris container; compiled SQL only."""
from phases.phase_01_foundation.sources import digest
from phases.phase_01_foundation.zetaris import Zetaris
from phases.phase_02_data.store import NAMESPACE, literal
from contracts import DisruptionSignal
from .registry import validate_signal


class SignalStore:
    def __init__(self, client):
        self.adapter, self.calls = Zetaris(client), 0

    def execute(self, sql, limit):
        if self.calls >= 5:
            raise ValueError('Signal Zetaris budget exhausted')
        self.calls += 1
        return self.adapter.submit_sql(sql, limit=limit)

    def publish(self, signals):
        values = sorted([validate_signal(s) for s in signals], key=lambda s: s.revision_id)
        if not 1 <= len(values) <= 100 or len({s.revision_id for s in values}) != len(values):
            raise ValueError('Signal publication requires 1..100 unique revisions')
        version = digest([s.model_dump(mode='json') for s in values])
        name = 'li_' + version
        table = NAMESPACE + '.' + name
        existing = self.execute('SHOW DATASOURCE VIEWS', 100)
        if not any(name == str(cell).lower() for row in existing['data'] for cell in row):
            rows = ','.join('(' + ','.join(literal(v) for v in (
                s.revision_id, s.event_id, s.model_dump_json())) + ')' for s in values)
            sql = f'CREATE SCHEMASTORE VIEW {name} WITH CONTAINER {NAMESPACE} AS SELECT * FROM VALUES {rows} AS data(revision_id,event_id,payload)'
            if len(sql.encode()) > 900_000:
                raise ValueError('Signal SQL response budget')
            self.execute(sql, 1)
        count = self.execute(f'SELECT COUNT(*) AS records FROM {table}', 1)
        if count['data'] not in ([[len(values)]], [[str(len(values))]]):
            raise ValueError('Signal publication count mismatch')
        response = self.execute(f'SELECT payload FROM {table} ORDER BY revision_id LIMIT 100', 100)
        queried = [DisruptionSignal.model_validate_json(row[0]) for row in response['data']]
        if [s.model_dump(mode='json') for s in queried] != [s.model_dump(mode='json') for s in values]:
            raise ValueError('Signal publication payload mismatch')
        return {'table': table, 'version': version, 'rows': len(values), 'verified': True,
                'queries': self.calls, 'signal_revisions': [s.revision_id for s in queried],
                'queried_events': [s.event_id for s in queried], 'mode': 'live-zetaris'}
