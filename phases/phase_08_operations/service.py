"""Bounded operator API; caller cannot choose files, URLs or inference providers."""
import sqlite3
import json
from datetime import date
from pydantic import ValidationError
from contracts.replay import document_time
from phases.phase_01_foundation.sources import digest
from phases.phase_06_helene.pipeline import replay_checkpoint
from .ingestion import REQUIRED, inspect, replay_importer
from .workflow import WorkflowStore


def handle(service, method, path, body, query):
    parts = path.strip('/').split('/')
    try:
        if method == 'POST' and parts == ['api', 'v1', 'helene', 'operations', 'import']:
            if query or not isinstance(body, dict) or set(body) != {'checkpoint', 'feeds', 'clocks'}:
                raise ValueError('IMPORT_SCHEMA_INVALID')
            if body['checkpoint'] not in {'2024-09-29', '2024-10-01', '2024-10-09', '2024-11-11'}:
                raise ValueError('UNKNOWN_CHECKPOINT')
            if not isinstance(body['feeds'], dict) or set(body['feeds']) != set(REQUIRED):
                raise ValueError('IMPORT_FEEDS_INVALID')
            if not isinstance(body['clocks'], dict) or set(body['clocks']) - {'schema_version', 'effective_at', 'exported_at', 'modeled_available_at'}:
                raise ValueError('IMPORT_CLOCKS_INVALID')
            if any(not isinstance(v, str) or len(v.encode()) > 4*1024*1024 for v in body['feeds'].values()):
                raise ValueError('IMPORT_BYTE_BUDGET')
            if not service.execution_lock.acquire(blocking=False):
                return 409, dict(error='INVESTIGATION_BUSY')
            try:
                root = service.root / 'phase8-imports' / digest(body)
                folder = root / body['checkpoint']
                folder.mkdir(parents=True, exist_ok=True)
                for feed, content in body['feeds'].items():
                    path = folder / (feed+'.csv')
                    if path.exists() and path.read_text() != content:
                        raise ValueError('IMMUTABLE_IMPORT_CONFLICT')
                    if not path.exists():
                        path.write_text(content)
                clocks = folder / 'clocks.json'
                if not clocks.exists():
                    clocks.write_text(json.dumps(body['clocks']))
                _, _, report = inspect(folder, document_time(date.fromisoformat(body['checkpoint'])))
                if not report['supports_forecast']:
                    return 422, dict(schema_version='8.1', error='OPERATIONAL_INPUT_REJECTED', readiness=report)
                case = replay_checkpoint(body['checkpoint'], operations=root, importer=replay_importer)
                try:
                    service.helene_store.get(case.investigation_id)
                    return 200, dict(investigation_id=case.investigation_id, readiness=report)
                except KeyError:
                    service.helene_store.save(case)
                return 201, dict(investigation_id=case.investigation_id, readiness=report)
            finally:
                service.execution_lock.release()
        if len(parts) < 6 or parts[:4] != ['api', 'v1', 'helene', 'cases']:
            raise KeyError('OPERATIONAL_ENDPOINT_NOT_FOUND')
        if query:
            raise ValueError('QUERY_NOT_SUPPORTED')
        store = WorkflowStore(service.store)
        case = service.helene_store.get(parts[4])
        if method == 'GET' and parts[5:] == ['operations']:
            return 200, store.view(case)
        if method == 'POST' and parts[5:] == ['operations', 'actions']:
            return 201, store.propose(case, body)
        if method == 'POST' and len(parts) == 9 and parts[5:7] == ['operations', 'actions'] and parts[8] == 'transition':
            return 200, store.transition(case, parts[7], body)
        return 404, dict(error='OPERATIONAL_ENDPOINT_NOT_FOUND')
    except KeyError:
        return 404, dict(error='OPERATIONAL_RECORD_NOT_FOUND')
    except sqlite3.IntegrityError:
        return 409, dict(error='OPERATIONAL_REVISION_CONFLICT')
    except OSError:
        return 422, dict(error='SOURCE_FILE_UNAVAILABLE')
    except (ValidationError, ValueError) as exc:
        code = str(exc)
        return 409 if code == 'REVISION_CONFLICT' else 422, dict(schema_version='8.1',
            error=code if code.isupper() and len(code) < 100 else 'OPERATIONAL_ADMISSION_INVALID')
