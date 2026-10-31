"""Verifier for the SIGMA fleet cockpit door (wave 2 lane 05, 2026-10-30).

Consumer: fleet_door.py /api/v1/sigma/fleet (the heartbeat-ledger door)
plus the /sigma_fleet.html cockpit page that fetches it. This file is the
door's named verifier, promised before landing in the Anti-Cow block of
watch/inbox/SIGMA_MAP_06_COCKPIT_20261030.md section 6. Covers: the live
route registers and serves 200, an absent or empty ledger is tolerated as an
empty fleet, stale rows fall through the G6 rule to DARK, enrolled ollama-*
providers.yaml rows with no heartbeat render DARK (and only those rows: the
bare `ollama` lane is not a lung), and the payload JSON shape the page
renders. Rollup thresholds are the lane 13 collector's own
(fleet_heartbeat_collect.py, same organ); here they are exercised through the
door's payload function exactly as the page consumes them.
"""
from __future__ import annotations

import datetime
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

ORGAN = Path(__file__).resolve().parent
if str(ORGAN) not in sys.path:
    sys.path.insert(0, str(ORGAN))

import fleet_door as dsrv  # noqa: E402
import fleet_heartbeat_collect as fbc  # noqa: E402

NOW = datetime.datetime(2026, 10, 30, 15, 0, 0, tzinfo=datetime.timezone.utc)


def card(minutes_ago: float, host: str = "aspire", proof_ok: bool = True) -> dict:
    generated = NOW - datetime.timedelta(minutes=minutes_ago)
    c = {
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
        c["generation"] = {"ok": True, "model": "gemma:2b", "seconds": 75.4, "reply": "READY"}
    else:
        c["generation"] = {"ok": False, "error": "no models pulled"}
    return c


def write_ledger(tmp_path: Path, cards: list[dict], name: str = "heartbeats.jsonl") -> Path:
    p = tmp_path / name
    rows = [
        {"received_at": fbc.iso_utc(NOW), "host": c["hostname"], "card": c}
        for c in cards
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


PROVIDERS_YAML = """
providers:
  anthropic:
    name: Anthropic
    kind: api
  ollama:
    name: Ollama (local)
    kind: openai_compat
  ollama-bob-2:
    name: Bob-2 lung
    kind: openai_compat
  ollama-ghost:
    name: enrolled but never seen
    kind: openai_compat
"""


def write_providers(tmp_path: Path) -> Path:
    p = tmp_path / "providers.yaml"
    p.write_text(PROVIDERS_YAML, encoding="utf-8")
    return p


# ---------- the payload function (what the door serves) ----------

def test_absent_ledger_tolerated_as_empty_fleet_with_enrolled_dark(tmp_path: Path) -> None:
    p = _sigma(tmp_path, ledger=tmp_path / "nope" / "heartbeats.jsonl", provs=write_providers(tmp_path))
    assert p["schema"] == "sigma_fleet.v1"
    # the bare `ollama` lane is not a lung; only ollama-* rows are enrolled
    assert p["enrolled_lungs"] == ["ollama-bob-2", "ollama-ghost"]
    assert p["summary"] == {"lungs": 2, "alive": 0, "warn": 0, "dark": 2}
    by_host = {row["host"]: row for row in p["lungs"]}
    assert set(by_host) == {"ollama-bob-2", "ollama-ghost"}
    for row in by_host.values():
        assert row["status"] == "dark"
        assert row["last_seen"] is None
        assert row["note"] == "never seen"


def test_empty_ledger_file_tolerated(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    ledger.write_text("", encoding="utf-8")
    p = _sigma(tmp_path, ledger=ledger, provs=write_providers(tmp_path))
    assert p["summary"]["lungs"] == 2  # the two enrolled ollama-* rows, DARK
    assert p["lungs"][0]["status"] == "dark"


def test_stale_rows_marked_dark_and_fresh_alive(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path, [card(2.0, host="aspire"), card(60.0, host="stale-box")])
    p = _sigma(tmp_path, ledger=ledger, provs=write_providers(tmp_path))
    by_host = {row["host"]: row for row in p["lungs"]}
    assert by_host["aspire"]["status"] == "alive"
    assert by_host["aspire"]["last_seen_age_min"] == 2.0
    assert by_host["stale-box"]["status"] == "dark"
    assert by_host["stale-box"]["last_seen_age_min"] == 60.0


def test_warn_band_between_grace_and_dark(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path, [card(30.0, host="mid")])
    p = _sigma(tmp_path, ledger=ledger, provs=tmp_path / "absent.yaml")
    assert p["lungs"][0]["status"] == "warn"


def test_latest_card_wins_per_host(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path, [card(90.0, host="flap"), card(3.0, host="flap")])
    p = _sigma(tmp_path, ledger=ledger, provs=tmp_path / "absent.yaml")
    assert p["summary"]["lungs"] == 1
    assert p["lungs"][0]["status"] == "alive"


def test_enrolled_alias_does_not_double_render_one_box(tmp_path: Path) -> None:
    # fleet_enroll names the row ollama-<flattened hostname>; the ledger keeps
    # the card hostname. One physical box must render once, alive.
    ledger = write_ledger(tmp_path, [card(5.0, host="Bob-2")])
    p = _sigma(tmp_path, ledger=ledger, provs=write_providers(tmp_path))
    hosts = [row["host"] for row in p["lungs"]]
    assert hosts == ["Bob-2", "ollama-ghost"]  # ollama-bob-2 matched its lung; only the ghost is dark
    assert p["summary"] == {"lungs": 2, "alive": 1, "warn": 0, "dark": 1}
    ghost = [row for row in p["lungs"] if row["host"] == "ollama-ghost"][0]
    assert ghost["status"] == "dark"


def test_only_ollama_dash_rows_enrolled(tmp_path: Path) -> None:
    # anthropic and the bare `ollama` row never become lung rows
    ledger = tmp_path / "heartbeats.jsonl"  # absent
    p = _sigma(tmp_path, ledger=ledger, provs=write_providers(tmp_path))
    assert "anthropic" not in p["enrolled_lungs"]
    assert "ollama" not in p["enrolled_lungs"]


def test_row_json_shape(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path, [card(10.0, host="aspire", proof_ok=False)])
    p = _sigma(tmp_path, ledger=ledger, provs=write_providers(tmp_path))
    row = p["lungs"][0]
    for key in (
        "host", "status", "last_seen", "last_seen_age_min", "models", "model_count",
        "ollama_version", "disk_free_gb", "mem_total", "tailscale_ip", "proof", "received_at",
    ):
        assert key in row, key
    assert row["tailscale_ip"] == "100.72.138.62"
    assert row["models"] == ["gemma:2b", "gemma4:latest"]
    assert row["model_count"] == 2
    assert row["proof"]["age_min"] == 10.0  # proof age == card age (probe ran at beat time)
    assert row["proof"]["ok"] is False
    assert row["proof"]["stale"] is False
    for key in (
        "schema", "generated_at", "beat_minutes", "dark_after_minutes",
        "alive_grace_minutes", "proof_stale_minutes", "ledger", "ledger_truncated",
        "enrolled_lungs", "summary", "lungs",
    ):
        assert key in p, key
    assert p["beat_minutes"] == 15
    assert p["dark_after_minutes"] == 45
    assert p["alive_grace_minutes"] == 20
    assert p["proof_stale_minutes"] == 150


def test_proof_stale_flag_after_window(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path, [card(200.0, host="oldproof")])
    p = _sigma(tmp_path, ledger=ledger, provs=tmp_path / "absent.yaml")
    row = p["lungs"][0]
    assert row["proof"]["stale"] is True
    assert row["status"] == "dark"  # 200 min is past the 45 min dark line too


def test_malformed_ledger_rows_skipped(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeats.jsonl"
    good = json.dumps({"received_at": fbc.iso_utc(NOW), "host": "aspire", "card": card(4.0)})
    ledger.write_text(
        "not json at all\n"
        + json.dumps({"host": "no-card"}) + "\n"
        + json.dumps({"received_at": fbc.iso_utc(NOW), "host": "x", "card": {"kind": "wrong"}}) + "\n"
        + good + "\n",
        encoding="utf-8",
    )
    p = _sigma(tmp_path, ledger=ledger, provs=tmp_path / "absent.yaml")
    assert [row["host"] for row in p["lungs"]] == ["aspire"]
    assert p["summary"]["alive"] == 1


def _sigma(tmp_path: Path, ledger: Path, provs: Path) -> dict:
    return dsrv.sigma_fleet_payload(ledger=ledger, providers_path=provs, now=NOW)


# ---------- the live routes (registration + no existing-route breakage) ----------

@pytest.fixture()
def server_url():
    httpd = dsrv.ThreadingHTTPServer(("127.0.0.1", 0), dsrv.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(base: str, path: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return r.status, r.read(), (r.headers.get("Content-Type") or "")


def test_sigma_fleet_route_serves_200(server_url) -> None:
    status, body, _ = _get(server_url, "/api/v1/sigma/fleet")
    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["schema"] == "sigma_fleet.v1"
    assert {"summary", "lungs", "enrolled_lungs"} <= set(payload)
    assert payload["summary"]["lungs"] == len(payload["lungs"])


def test_cockpit_page_route_serves_html(server_url) -> None:
    status, body, ctype = _get(server_url, "/sigma_fleet.html")
    assert status == 200
    assert "text/html" in ctype
    assert b"/api/v1/sigma/fleet" in body
    status, _, _ = _get(server_url, "/sigma_fleet")
    assert status == 200


def test_index_route_serves(server_url) -> None:
    status, _, ctype = _get(server_url, "/")
    assert status == 200
    assert "text/html" in ctype
    assert b"/sigma_fleet.html" in _get(server_url, "/")[1]
