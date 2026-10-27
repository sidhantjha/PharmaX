"""A clean October 1 checkout must not require future agency document bytes."""
from datetime import datetime, timezone
import shutil
import pytest
from phases.phase_07_reconciliation.sources import ROOT, load_public


def test_future_sources_not_read_or_admitted(tmp_path):
    shutil.copyfile(ROOT / 'manifest.json', tmp_path / 'manifest.json')
    assert load_public(datetime(2024, 10, 2, tzinfo=timezone.utc), root=tmp_path) == []


def test_required_source_still_fails_without_original(tmp_path):
    shutil.copyfile(ROOT / 'manifest.json', tmp_path / 'manifest.json')
    with pytest.raises(FileNotFoundError):
        load_public(datetime(2024, 10, 13, tzinfo=timezone.utc), root=tmp_path)
