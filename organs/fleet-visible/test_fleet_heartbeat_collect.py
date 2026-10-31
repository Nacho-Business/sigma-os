"""Verifier for fleet_heartbeat_collect.py (organs/fleet-visible, the coordinator heartbeat
collector, SIGMA wave 2 lane 13).

Named in the tool's docstring as its verifier (Consumer Law). Covers: fixture
cards fresh/stale/dark/malformed, the G6 collector state transitions
(never_seen -> alive -> warn -> dark), jsonl append idempotency on
(host, generated), the 7-day retention prune, and the cockpit lane-06 fleet
view feed shape. Spec: watch/inbox/SIGMA_MAP_08_HEALTH_20261030.md (collector
first, then the G6 dark rule) and watch/inbox/SIGMA_MAP_06_COCKPIT_20261030.md
section 3a (the feed columns this view serves).
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

organ = str(Path(__file__).resolve().parent)
if organ not in __import__("sys").path:
    __import__("sys").path.insert(0, organ)

import fleet_heartbeat_collect as fbc  # noqa: E402

NOW = datetime.datetime(2026, 10, 30, 15, 0, 0, tzinfo=datetime.timezone.utc)


def card_at(minutes_ago: float, host: str = "aspire", proof_ok: bool = True) -> dict:
    generated = NOW - datetime.timedelta(minutes=minutes_ago)
    card = {
        "kind": "mag_lung_introduction.v1",
        "generated": fbc.iso_utc(generated),
        "hostname": host,
        "platform": "Linux 7.0.0-42-generic (x86_64)",
        "cpu_count": 4,
        "mem_total": "3.3G",
        "disk_free_gb": 28.4,
        "tailscale_ip": "100.72.138.62",
        "ollama": {"version": "0.40.13", "models": ["gemma:2b", "gemma4:latest"]},
    }
    if proof_ok:
        card["generation"] = {"ok": True, "model": "gemma:2b", "seconds": 75.4, "reply": "READY"}
    else:
        card["generation"] = {"ok": False, "error": "no models pulled"}
    return card


def write_card(tmp_path: Path, card: object, name: str = "card.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(card), encoding="utf-8")
    return p


def read_rows(ledger: Path) -> list[dict]:
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- card validation ----------

def test_script_compiles() -> None:
    import py_compile

    py_compile.compile(str(Path(__file__).resolve().parent / "fleet_heartbeat_collect.py"), doraise=True)


def test_validate_accepts_good_card() -> None:
    ok, reason = fbc.validate_card(card_at(2))
    assert ok, reason


@pytest.mark.parametrize(
    "bad",
    [
        "not a dict at all",
        [{"kind": "mag_lung_introduction.v1"}],
        {"kind": "some_other.v1", "hostname": "x", "generated": "2026-10-30T14:00:00Z"},
        {"kind": "mag_lung_introduction.v1", "generated": "2026-10-30T14:00:00Z"},
        {"kind": "mag_lung_introduction.v1", "hostname": "x", "generated": "not-a-date"},
        {"kind": "mag_lung_introduction.v1", "hostname": "x", "generated": 42},
    ],
)
def test_validate_rejects_malformed(bad: object) -> None:
    ok, reason = fbc.validate_card(bad)
    assert not ok
    assert reason != "ok"


# ---------- jsonl append + idempotency ----------

def test_append_row_shape(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    appended, skipped = fbc.append_cards([card_at(2)], ledger, now=NOW)
    assert (appended, skipped) == (1, 0)
    rows = read_rows(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"received_at", "host", "card"}
    assert row["host"] == "aspire"
    assert row["received_at"] == fbc.iso_utc(NOW)
    assert row["card"]["kind"] == "mag_lung_introduction.v1"


def test_append_is_idempotent_on_same_card(tmp_path: Path) -> None:
    """Re-feeding the identical card (host + generated) never double-appends."""
    ledger = tmp_path / "heartbeats.jsonl"
    same_card = card_at(2)
    fbc.append_cards([same_card], ledger, now=NOW)
    appended, skipped = fbc.append_cards([same_card], ledger, now=NOW)
    assert (appended, skipped) == (0, 1)
    assert len(read_rows(ledger)) == 1


def test_append_two_files_same_card_single_row(tmp_path: Path) -> None:
    """Two distinct card files carrying the same beat land once."""
    ledger = tmp_path / "heartbeats.jsonl"
    a = write_card(tmp_path, card_at(2), "a.json")
    b = write_card(tmp_path, card_at(2), "b.json")
    cards, rejects = fbc.load_cards_from_args([str(a), str(b)])
    assert not rejects
    appended, skipped = fbc.append_cards(cards, ledger, now=NOW)
    assert (appended, skipped) == (1, 1)
    assert len(read_rows(ledger)) == 1


def test_append_new_beats_append_fresh_rows(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    fbc.append_cards([card_at(30)], ledger, now=NOW)
    appended, skipped = fbc.append_cards([card_at(2)], ledger, now=NOW)
    assert (appended, skipped) == (1, 0)
    rows = read_rows(ledger)
    assert len(rows) == 2
    assert rows[0]["card"]["generated"] != rows[1]["card"]["generated"]


def test_directory_glob_loads_json_files(tmp_path: Path) -> None:
    (tmp_path / "inbox").mkdir()
    write_card(tmp_path / "inbox", card_at(2, host="aspire"), "one.json")
    write_card(tmp_path / "inbox", card_at(10, host="bob-2"), "two.json")
    (tmp_path / "inbox" / "not_a_card.txt").write_text("skip me", encoding="utf-8")
    cards, rejects = fbc.load_cards_from_args([str(tmp_path / "inbox")])
    assert len(cards) == 2
    assert not rejects
    assert {c["hostname"] for c in cards} == {"aspire", "bob-2"}


def test_malformed_file_is_rejected_never_appended(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    good = write_card(tmp_path, card_at(2), "good.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    wrong_kind = write_card(tmp_path, {"kind": "nope"}, "wrong.json")
    cards, rejects = fbc.load_cards_from_args([str(good), str(bad), str(wrong_kind)])
    assert len(cards) == 1
    assert len(rejects) == 2
    appended, _ = fbc.append_cards(cards, ledger, now=NOW)
    assert appended == 1
    rows = read_rows(ledger)
    assert len(rows) == 1
    assert rows[0]["card"]["hostname"] == "aspire"


# ---------- state transitions (G6) ----------

def test_status_transitions_never_seen_to_dark() -> None:
    assert fbc.status_for(None) == "dark"


def test_status_transitions_alive_warn_dark() -> None:
    assert fbc.status_for(2.0) == "alive"    # fresh: within 15 min beat + grace
    assert fbc.status_for(20.0) == "alive"   # grace boundary inclusive
    assert fbc.status_for(30.0) == "warn"    # stale: past grace, under 3 missed beats
    assert fbc.status_for(45.0) == "dark"    # 15 min x 3 beats = dark (lane T G6)
    assert fbc.status_for(60.0) == "dark"


def test_constants_match_the_spec() -> None:
    """The thresholds are lane T numbers, not invented ones."""
    assert fbc.BEAT_MINUTES == 15
    assert fbc.DARK_BEATS == 3
    assert fbc.DARK_AFTER_MIN == 45
    assert fbc.ALIVE_GRACE_MIN == 20
    assert fbc.PROOF_STALE_MIN == 150
    assert fbc.RETENTION_DAYS == 7


# ---------- fleet view ----------

def _collect(ledger: Path, cards: list[dict]) -> None:
    fbc.append_cards(cards, ledger, now=NOW)


def test_fleet_view_shape_and_latest_card_per_host(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    _collect(ledger, [card_at(30, host="aspire"), card_at(2, host="aspire"), card_at(5, host="bob-2")])
    view = fbc.build_fleet_view(ledger, now=NOW)
    assert view["generated_at"] == fbc.iso_utc(NOW)
    assert view["summary"] == {"lungs": 2, "alive": 2, "warn": 0, "dark": 0}
    by_host = {row["host"]: row for row in view["lungs"]}
    assert set(by_host) == {"aspire", "bob-2"}
    aspire = by_host["aspire"]
    # the LATEST card wins: 2 min old, not the 30 min one
    assert aspire["status"] == "alive"
    assert aspire["last_seen_age_min"] == 2.0
    assert aspire["models"] == ["gemma:2b", "gemma4:latest"]
    assert aspire["model_count"] == 2
    assert aspire["ollama_version"] == "0.40.13"
    assert aspire["disk_free_gb"] == 28.4
    assert aspire["mem_total"] == "3.3G"
    assert aspire["tailscale_ip"] == "100.72.138.62"
    assert aspire["proof"] == {"ok": True, "model": "gemma:2b", "seconds": 75.4, "age_min": 2.0, "stale": False}


def test_fleet_view_dark_and_warn_states(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    _collect(ledger, [card_at(50, host="aspire"), card_at(30, host="bob-2")])
    view = fbc.build_fleet_view(ledger, now=NOW)
    assert view["summary"] == {"lungs": 2, "alive": 0, "warn": 1, "dark": 1}
    by_host = {row["host"]: row for row in view["lungs"]}
    assert by_host["aspire"]["status"] == "dark"
    assert by_host["bob-2"]["status"] == "warn"


def test_fleet_view_configured_never_seen_lung_is_dark(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    _collect(ledger, [card_at(2, host="aspire")])
    view = fbc.build_fleet_view(ledger, now=NOW, configured_lungs=["aspire", "third-lung"])
    assert view["summary"] == {"lungs": 2, "alive": 1, "warn": 0, "dark": 1}
    by_host = {row["host"]: row for row in view["lungs"]}
    ghost = by_host["third-lung"]
    assert ghost["status"] == "dark"
    assert ghost["last_seen"] is None
    assert ghost["note"] == "never seen"


def test_fleet_view_proof_stale_flag(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    _collect(ledger, [card_at(160, host="aspire"), card_at(160, host="bob-2", proof_ok=False)])
    view = fbc.build_fleet_view(ledger, now=NOW)
    by_host = {row["host"]: row for row in view["lungs"]}
    assert by_host["aspire"]["proof"]["stale"] is True
    assert by_host["bob-2"]["proof"]["stale"] is True
    assert by_host["bob-2"]["proof"]["ok"] is False


def test_fleet_view_empty_ledger(tmp_path: Path) -> None:
    view = fbc.build_fleet_view(tmp_path / "absent.jsonl", now=NOW)
    assert view["summary"] == {"lungs": 0, "alive": 0, "warn": 0, "dark": 0}
    assert view["lungs"] == []


# ---------- retention prune ----------

def test_prune_drops_rows_past_retention(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    old = card_at(0, host="old-box")
    # the row was RECEIVED 9 days ago; retention keys on received_at
    fbc.append_cards([old], ledger, now=NOW - datetime.timedelta(days=9))
    fbc.append_cards([card_at(2, host="aspire")], ledger, now=NOW)
    removed = fbc.prune_ledger(ledger, days=7, now=NOW)
    assert removed == 1
    rows = read_rows(ledger)
    assert len(rows) == 1
    assert rows[0]["card"]["hostname"] == "aspire"


def test_prune_keeps_current_ledger_untouched(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    _collect(ledger, [card_at(2)])
    before = ledger.read_text(encoding="utf-8")
    assert fbc.prune_ledger(ledger, days=7, now=NOW) == 0
    assert ledger.read_text(encoding="utf-8") == before


# ---------- fleet config ----------

def test_fleet_config_flat_mapping(tmp_path: Path) -> None:
    cfg = tmp_path / "fleet.json"
    cfg.write_text(json.dumps({"aspire": "bob"}), encoding="utf-8")
    lungs = fbc.load_fleet_config(str(cfg))
    assert lungs == [{"name": "aspire", "ssh_alias": "bob",
                      "remote_root": fbc.DEFAULT_REMOTE_ROOT}]


def test_fleet_config_rich_form(tmp_path: Path) -> None:
    cfg = tmp_path / "fleet.json"
    cfg.write_text(json.dumps({"lungs": [
        {"name": "aspire", "ssh_alias": "bob", "remote_root": "~/repo"},
    ]}), encoding="utf-8")
    lungs = fbc.load_fleet_config(str(cfg))
    assert lungs == [{"name": "aspire", "ssh_alias": "bob", "remote_root": "~/repo"}]
