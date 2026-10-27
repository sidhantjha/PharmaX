from datetime import datetime, timedelta, timezone
import json
import os
from unittest.mock import Mock
import httpx
import pytest
from phases.phase_01_foundation.http import ServiceError
from phases.phase_02_data.snapshots import Snapshots
from phases.phase_04_agents.live_intelligence.sources import (
    MARAD_INDEX,
    MARAD_PILOT,
    GDELT_URL,
    Fetcher,
    parse_marad,
    parse_gdelt,
    frozen,
)
from phases.phase_04_agents.live_intelligence.registry import Registry, lifecycle
from phases.phase_04_agents.live_intelligence.scenario import build_current
from phases.phase_04_agents.live_intelligence.matching import match_dependency
from phases.phase_04_agents.live_intelligence.pipeline import investigate_signal
from phases.phase_04_agents.live_intelligence.polling import refresh
from phases.phase_04_agents.live_intelligence.zetaris import SignalStore

NOW = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)
NODE = os.environ.get("PHARMAX_NODE", "node")
INDEX = (
    b"<table><tr><td>2026-013-Red Sea</td><td>Active</td><td>09/23/2026</td><td>03/22/2027</td></tr></table>"
)
DETAIL = b"<h1>2026-013-Red Sea, Bab el Mandeb Strait, Gulf of Aden, Arabian Sea-Houthi Attacks on Commercial Vessels</h1><p>This advisory supersedes and cancels U.S. Maritime Advisory 2026-006.</p><p>Threat to commercial ships.</p><p>Status: Active</p>"
NEWS = {
    "articles": [
        {
            "url": "https://example.org/red-sea",
            "title": "Red Sea shipping disruption",
            "domain": "example.org",
            "seendate": "20261024T120000Z",
        }
    ]
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", Mock(side_effect=AssertionError("NETWORK DENIED"))
    )


@pytest.fixture
def snapshots(tmp_path):
    return Snapshots(tmp_path / "snapshots")


@pytest.fixture
def source_signal(snapshots):
    a = snapshots.save("curated-document", MARAD_INDEX, INDEX, "text/html", observed_at=NOW.isoformat())
    b = snapshots.save("curated-document", MARAD_PILOT, DETAIL, "text/html", observed_at=NOW.isoformat())
    return parse_marad(a, b)


def test_parser_trust_and_replay(snapshots, source_signal):
    assert source_signal.event_id == "marad:2026-013"
    assert source_signal.supersedes == ["marad:2026-006"]
    assert source_signal.source_published_on is None
    assert str(source_signal.effective_on) == "2026-09-23"
    assert source_signal.observed_event_time is None
    assert source_signal.confirmation == "official_notice"
    assert all(e.observed_at == NOW for e in source_signal.evidence)
    keys = [e.raw_ref.split(":", 1)[1] for e in source_signal.evidence]
    assert frozen(snapshots, {"marad": keys}) == [source_signal]
    p = snapshots.root / "raw" / f"{source_signal.evidence[0].content_sha256}.bin"
    p.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        frozen(snapshots, {"marad": keys})


def test_gdelt_is_not_verification(snapshots):
    s = snapshots.save(
        "curated-document",
        GDELT_URL,
        json.dumps(NEWS).encode(),
        "application/json",
        observed_at=NOW.isoformat(),
    )
    signal = parse_gdelt(s)[0]
    assert signal.confirmation == "unverified_media" and signal.source_published_at is None
    assert signal.indexed_at < NOW and signal.geography == []
    assert not signal.impacted_assets
    bundle, dep = build_current(NOW.date(), NOW)
    assert match_dependency(signal, dep, NOW).status == "POSSIBLE"


def test_changed_vs_duplicate_and_history(snapshots, source_signal, tmp_path):
    registry = Registry(tmp_path / "events.sqlite")
    assert len(registry.ingest([source_signal])) == 1
    later = NOW + timedelta(hours=6)
    a = snapshots.save("curated-document", MARAD_INDEX, INDEX, "text/html", observed_at=later.isoformat())
    b = snapshots.save("curated-document", MARAD_PILOT, DETAIL, "text/html", observed_at=later.isoformat())
    same = parse_marad(a, b)
    assert same.revision_id == source_signal.revision_id
    assert not registry.ingest([same])
    cancelled = parse_marad(
        snapshots.save(
            "curated-document",
            MARAD_INDEX,
            INDEX.replace(b"Active", b"Cancelled"),
            "text/html",
            observed_at=later.isoformat(),
        ),
        b,
    )
    assert cancelled.revision_id != source_signal.revision_id
    registry.ingest([cancelled])
    assert registry.latest(NOW)[0].status == "active"
    assert registry.latest(later)[0].status == "cancelled"
    assert len(registry.revisions()) == 2
    assert not registry.latest(NOW - timedelta(seconds=1))
    assert lifecycle(source_signal, NOW + timedelta(days=180)) == "expired"
    prior = source_signal.model_copy(update={"event_id": "marad:2026-006"})
    assert lifecycle(prior, NOW, [source_signal]) == "superseded"


def test_bounded_fetch_and_rate_errors(snapshots):
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(200, content=INDEX if str(req.url) == MARAD_INDEX else DETAIL)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        fetcher = Fetcher(snapshots, client, max_calls=2)
        assert fetcher.fetch("marad")[0].event_id == "marad:2026-013"
        with pytest.raises(ServiceError, match="PUBLIC_FETCH_BUDGET"):
            fetcher.fetch("gdelt")
        with pytest.raises(ServiceError, match="SOURCE_URL_DENIED"):
            fetcher.get("https://example.org/arbitrary")
    assert len(calls) == 2
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "120"}))
    ) as client:
        with pytest.raises(ServiceError) as error:
            Fetcher(snapshots, client).fetch("gdelt")
        assert error.value.diagnostics["retry_after_seconds"] == 120


def test_gdelt_request_is_closed(snapshots):
    def handle(req):
        assert req.url.params["maxrecords"] == "12"
        assert req.url.params["mode"] == "artlist" and req.url.params["format"] == "json"
        assert "pharmaceutical manufacturing" in req.url.params["query"]
        return httpx.Response(200, json={"articles": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert Fetcher(snapshots, client).fetch("gdelt") == []


def test_matching_controls(source_signal):
    bundle, dep = build_current(NOW.date(), NOW)
    matched = match_dependency(source_signal, dep, NOW)
    assert matched.status == "MATCHED" and not matched.observed_shipment_delay
    assert "red-sea" in matched.matched_legs
    assert match_dependency(source_signal, dep, NOW - timedelta(days=1)).status == "UNMATCHED"
    for change in (
        {"corridors": ["black-sea"], "geography": []},
        {"status": "cancelled"},
        {"expires_on": NOW.date() - timedelta(days=1)},
    ):
        assert match_dependency(source_signal.model_copy(update=change), dep, NOW).status == "UNMATCHED"
    passed = dep.model_copy(deep=True)
    for leg in passed.legs:
        leg.starts_on -= timedelta(days=90)
        leg.ends_on -= timedelta(days=90)
    assert match_dependency(source_signal, passed, NOW).status == "UNMATCHED"
    with pytest.raises(ValueError):
        build_current(NOW.date() + timedelta(days=1), NOW)


def test_risk_mitigation_memory_and_replay(source_signal, tmp_path):
    bundle, dep = build_current(NOW.date(), NOW)
    before = bundle.model_dump_json()
    a = investigate_signal(source_signal, bundle, dep, tmp_path / "a", node=NODE)
    b = investigate_signal(source_signal, bundle, dep, tmp_path / "b", node=NODE)
    assert a.status == "complete", a.error_code
    assert a.model_dump_json() == b.model_dump_json()
    assert a.baseline.forecast.total_unmet == 0
    assert {s.delay_days: s.assessment.forecast.total_unmet for s in a.sensitivity} == {0: 0, 3: 0, 7: 24}
    assert a.mitigation.recommendation_id == "north-conservative"
    best = next(
        e for e in a.mitigation.evaluations if e.candidate.candidate_id == a.mitigation.recommendation_id
    )
    assert best.remaining_unmet == 0 and best.quantity_moved == 24
    assert all(i.effective.unmet_vials == 0 for i in best.impacts)
    assert any("DONOR_SHORTAGE" in [f.code for f in e.failures] for e in a.mitigation.evaluations)
    assert a.mitigation.memory_ids
    assert any(e["operation"] == "hmem.exchange" and e["data"].get("writes") for e in a.events)
    assert any(e["operation"] == "coordinator.replanning" and e["data"]["rejected"] for e in a.events)
    assert bundle.model_dump_json() == before
    assert bundle.scenario_as_of.year == 2026
    assert all(s.assumption_label == "simulated" and s.result_label == "calculated" for s in a.sensitivity)


def test_unrelated_never_dispatches_agents(source_signal, tmp_path, monkeypatch):
    bundle, dep = build_current(NOW.date(), NOW)
    denied = Mock(side_effect=AssertionError("NO AGENT CALL"))
    monkeypatch.setattr("phases.phase_04_agents.live_intelligence.pipeline.investigate_mitigation", denied)
    r = investigate_signal(
        source_signal.model_copy(update={"corridors": [], "geography": []}), bundle, dep, tmp_path, node=NODE
    )
    assert r.status == "unmatched" and r.mitigation is None and not r.sensitivity
    denied.assert_not_called()


def test_poll_backoff_duplicate_and_no_models(source_signal, tmp_path):
    registry = Registry(tmp_path / "events.sqlite")
    fetcher = Mock(receipts=[], fetch=Mock(return_value=[source_signal]))
    bundle, dep = build_current(NOW.date(), NOW)
    first = refresh(registry, fetcher, NOW, sources=["marad"], dependency=dep)
    assert first[0]["relevant_revisions"] == [source_signal.revision_id]
    assert refresh(registry, fetcher, NOW + timedelta(seconds=3), sources=["marad"], force=True) == []
    again = refresh(registry, fetcher, NOW + timedelta(hours=7), sources=["marad"], dependency=dep)
    assert not again[0]["new_revisions"]
    fetcher.fetch.side_effect = ServiceError("HTTP_429", diagnostics={"retry_after_seconds": 600})
    failed = refresh(registry, fetcher, NOW + timedelta(hours=14), sources=["marad"])
    assert failed[0]["status"] == "failed"
    state = registry.statuses()[0]
    assert state.last_success_at == NOW + timedelta(hours=7)
    assert state.next_check_at == NOW + timedelta(hours=14, seconds=600)
    assert state.monitoring == "last-check-only"
    assert (
        refresh(registry, fetcher, NOW + timedelta(hours=14, seconds=30), sources=["marad"], force=True) == []
    )
    registry = Registry(tmp_path / "events.sqlite")
    assert registry.statuses()[0].consecutive_failures == 1


def test_zetaris_immutable_publication_and_query(source_signal):
    store = SignalStore(Mock())
    store.adapter.submit_sql = Mock(
        side_effect=[
            {"data": []},
            {"data": []},
            {"data": [[1]]},
            {"data": [[source_signal.model_dump_json()]]},
        ]
    )
    receipt = store.publish([source_signal])
    assert receipt["verified"] and receipt["rows"] == 1 and store.calls == 4
    sql = store.adapter.submit_sql.call_args_list[1].args[0]
    assert "CREATE SCHEMASTORE VIEW li_" in sql and "pharmax_p2_data" in sql
    assert source_signal.title not in sql  # Reuses hex literals; source cannot become SQL syntax.
    assert "DROP" not in sql and "REPLACE" not in sql
    store = SignalStore(Mock())
    store.adapter.submit_sql = Mock(side_effect=[{"data": []}, {"data": []}, {"data": [[1]]}, {"data": []}])
    with pytest.raises(ValueError, match="payload mismatch"):
        store.publish([source_signal])


@pytest.mark.parametrize("delays", [(), (1, 7), (0, 7, 7), (0, 22), (0, True)])
def test_invalid_delay_budgets(source_signal, tmp_path, delays):
    bundle, dep = build_current(NOW.date(), NOW)
    with pytest.raises(ValueError):
        investigate_signal(source_signal, bundle, dep, tmp_path, node=NODE, delays=delays)


def test_utility_headings_and_source_constraints(snapshots):
    a = snapshots.save("curated-document", MARAD_INDEX, INDEX, "text/html", observed_at=NOW.isoformat())
    b = snapshots.save(
        "curated-document",
        MARAD_PILOT,
        b"<h1>USA Banner</h1><h1>In This Section</h1>" + DETAIL,
        "text/html",
        observed_at=NOW.isoformat(),
    )
    signal = parse_marad(a, b)
    assert signal.title.startswith("2026-013-Red Sea")
    bad = snapshots.save(
        "curated-document", "https://example.org/not-marad", DETAIL, "text/html", observed_at=NOW.isoformat()
    )
    with pytest.raises(ServiceError, match="MARAD_ORIGIN_INVALID"):
        parse_marad(a, bad)


def test_product_negative_control_and_revision_integrity(source_signal, tmp_path):
    bundle, dep = build_current(NOW.date(), NOW)
    wrong = dep.model_copy(update={"product_id": "sim-carboplatin-150"})
    assert match_dependency(source_signal, wrong, NOW, bundle=bundle).status == "UNMATCHED"
    registry = Registry(tmp_path / "events.sqlite")
    with pytest.raises(ValueError, match="revision hash"):
        registry.ingest([source_signal.model_copy(update={"title": "tampered"})])
    key = "a" * 64
    assert registry.queue(key) and not registry.queue(key)
    assert registry.claim(key) and not registry.claim(key)


def test_read_services_historical_isolation_and_freshness(source_signal, tmp_path):
    from phases.phase_04_agents.live_intelligence.service import IntelligenceService

    registry = Registry(tmp_path / "events.sqlite")
    registry.ingest([source_signal])
    fetcher = Mock(receipts=[], fetch=Mock(return_value=[source_signal]))
    refresh(registry, fetcher, NOW, sources=["marad"])
    service = IntelligenceService(registry)
    assert service.read("signals", cutoff=NOW)["items"][0]["signal"]["provider"] == "marad"
    assert service.read("citations", cutoff=NOW)["items"]
    assert not service.read("signals", cutoff=NOW - timedelta(seconds=1))["items"]
    assert (
        service.read("sources", cutoff=NOW - timedelta(seconds=1))["items"][0]["history_available"] is False
    )
    assert service.read("sources", cutoff=NOW + timedelta(days=1))["items"][0]["stale"] is True
    for name in ("matches", "risk", "forecasts", "activity", "mitigations"):
        assert service.read(name, cutoff=NOW)["items"] == []
    with pytest.raises(ValueError):
        service.read("refresh")
    with pytest.raises(ValueError):
        service.read("signals", limit=1000)


def test_retained_real_marad_capture_and_zetaris_receipt():
    from pathlib import Path

    root = Path(__file__).resolve().parents[4] / "docs/evidence/pharmax-phase4c"
    manifest = json.loads((root / "manifest.json").read_text())
    signals = frozen(Snapshots(root / "snapshots"), manifest)
    official = next(s for s in signals if s.provider == "marad")
    assert official.event_id == "marad:2026-013" and official.confirmation == "official_notice"
    assert official.retrieved_at.date().isoformat() == "2026-10-24"
    assert manifest["zetaris"]["verified"] and official.revision_id in manifest["zetaris"]["signal_revisions"]
    assert any(s["last_error"] == "HTTP_429" and s["source"] == "gdelt" for s in manifest["source_status"])
    bundle, dep = build_current(official.retrieved_at.date(), official.retrieved_at)
    assert match_dependency(official, dep, official.retrieved_at, bundle=bundle).status == "MATCHED"


@pytest.mark.parametrize("case", ["timeout", "oversized", "redirect", "bad_json"])
def test_fetch_failures_never_fall_back(snapshots, case):
    def handle(req):
        if case == "timeout":
            raise httpx.ReadTimeout("untrusted provider details")
        if case == "oversized":
            return httpx.Response(200, content=b"x" * 512001)
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.org/arbitrary"})
        return httpx.Response(200, content=b"not JSON")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        fetcher = Fetcher(snapshots, client)
        with pytest.raises(ServiceError):
            fetcher.fetch("gdelt")
        assert fetcher.calls == 1


def test_poll_queues_once_and_keeps_model_free(source_signal, tmp_path):
    from phases.phase_04_agents.live_intelligence.registry import investigation_key

    reg = Registry(tmp_path / "jobs.sqlite")
    bundle, dep = build_current(NOW.date(), NOW)
    f = Mock(receipts=[], fetch=Mock(return_value=[source_signal]))
    refresh(reg, f, NOW, sources=["marad"], dependency=dep)
    key = investigation_key(source_signal, dep)
    assert reg.claim(key)
    assert not reg.claim(key)
    refresh(reg, f, NOW + timedelta(hours=7), sources=["marad"], dependency=dep)
    assert not reg.claim(key)  # Repeat fetch did not create another work claim.


def test_raw_source_changes_preserved_without_invented_new_claims(snapshots, source_signal, tmp_path):
    reg = Registry(tmp_path / "events.sqlite")
    reg.ingest([source_signal])
    later = NOW + timedelta(hours=7)
    a = snapshots.save("curated-document", MARAD_INDEX, INDEX, "text/html", observed_at=later.isoformat())
    b = snapshots.save(
        "curated-document",
        MARAD_PILOT,
        DETAIL + b"<p>Changed text awaiting further review.</p>",
        "text/html",
        observed_at=later.isoformat(),
    )
    changed = parse_marad(a, b)
    assert changed.revision_id == source_signal.revision_id  # Bounded extracted claims remain the same.
    fetcher = Mock(receipts=[a[0], b[0]], fetch=Mock(return_value=[changed]))
    result = refresh(reg, fetcher, later, sources=["marad"])
    assert not result[0]["new_revisions"]
    status = reg.statuses()[0]
    assert status.raw_content_changed and status.content_needs_review
    assert len(reg.revisions()) == 1
    assert (
        reg.latest(later)[0].evidence == source_signal.evidence
    )  # Never silently replace the original evidence.


def test_populated_read_services_and_stale_decisions(source_signal, tmp_path):
    from phases.phase_04_agents.live_intelligence.service import IntelligenceService
    from phases.phase_04_agents.live_intelligence.registry import investigation_key

    bundle, dep = build_current(NOW.date(), NOW)
    run = investigate_signal(source_signal, bundle, dep, tmp_path / "mem", node=NODE)
    reg = Registry(tmp_path / "events.sqlite")
    reg.ingest([source_signal])
    key = investigation_key(source_signal, dep)
    reg.queue(key)
    reg.claim(key)
    reg.finish(key, run)
    service = IntelligenceService(reg)
    for resource in ("signals", "citations", "matches", "risk", "forecasts", "activity", "mitigations"):
        reply = service.read(resource, cutoff=NOW)
        assert reply["items"] and reply["latest_decision_state"] == "current-for-retained-revision"
    assert not service.read("risk", cutoff=NOW - timedelta(seconds=1))["items"]
    expired = service.read("risk", cutoff=NOW + timedelta(days=180))
    assert expired["latest_decision_state"] == "stale-revision-or-expired"
