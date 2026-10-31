"""Verifier for organs/cost-priced/fleet_cost_ledger.py (the fleet cost + yield ledger).

Named in the tool's docstring as its verifier (Consumer Law). Covers the job-row
schema parse (v1 rows and raw ollama shapes), the two-state power math against
the lane O cost table, the break-even projection against lane C section (c),
rollup math (duty, measured vs estimated tokens, monthly projection, marginal
$/1M), the record append path, and a CLI smoke test.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ORGAN = str(Path(__file__).resolve().parent)
if ORGAN not in sys.path:
    sys.path.insert(0, ORGAN)

import fleet_cost_ledger as fcl  # noqa: E402


def test_script_compiles() -> None:
    import py_compile

    src = Path(__file__).resolve().parent / "fleet_cost_ledger.py"
    py_compile.compile(str(src), doraise=True)


# ------------------------------------------------------------- power math


def test_monthly_cost_matches_lane_o_table() -> None:
    """Lane O power table (laptop 5W idle / 25W load, 720h, $0.15/kWh)."""
    assert fcl.monthly_cost(5, 25, 0.0) == pytest.approx(0.54, abs=1e-9)
    assert fcl.monthly_cost(5, 25, 0.05) == pytest.approx(0.65, abs=0.005)
    assert fcl.monthly_cost(5, 25, 0.10) == pytest.approx(0.76, abs=0.005)
    assert fcl.monthly_cost(5, 25, 1.0) == pytest.approx(2.70, abs=1e-9)


def test_breakeven_cross_checks_lane_c_section_c() -> None:
    """Lane C break-even table: 2015 laptop vs GPT-5 nano output = 9.1% duty.

    Lane C constants (idle 10, load 35, 15 tok/s, 730h month, $0.16/kWh) must
    reproduce their d* = 1.168 / (39.42 x 0.40 - 25 x 0.1168) line.
    """
    d = fcl.breakeven_duty(10, 35, 15.0, 0.40, month_hours=730, tariff=0.16)
    assert d is not None
    assert d == pytest.approx(0.0909, abs=0.0002)


def test_breakeven_never_cases_return_none() -> None:
    """Lane C: old desktop vs Haiku 5.5 blended 'never (fails at d=1)'."""
    assert fcl.breakeven_duty(60, 180, 25.0, 0.30, month_hours=730, tariff=0.16) is None
    # denominator non-positive: token rate too slow for the marginal watts
    assert fcl.breakeven_duty(5, 25, 0.5, 0.40) is None


def test_marginal_usd_per_m_is_duty_independent() -> None:
    """Lane C already-on case: laptop marginal $0.075/1M at their constants."""
    assert fcl.marginal_usd_per_m(10, 35, 15.0, tariff=0.16) == pytest.approx(0.074, abs=0.002)
    assert fcl.marginal_usd_per_m(5, 25, 15.0) == pytest.approx(0.0556, abs=0.001)
    a = fcl.marginal_usd_per_m(5, 25, 15.0, charger_loss=1.15)
    b = fcl.marginal_usd_per_m(5, 25, 15.0, charger_loss=1.0)
    assert a == pytest.approx(b * 1.15)


def test_est_wh() -> None:
    """Bob's measured probe: 25W x 104.7s."""
    assert fcl.est_wh(25, 104.7) == pytest.approx(0.7271, abs=0.001)


# ------------------------------------------------------------- schema parse


def test_parse_v1_row() -> None:
    row = fcl.parse_job_row({
        "schema": "fleet_cost_job.v1", "ts": "2026-10-30T12:00:00Z",
        "lung": "bob", "model": "qwen2.5:3b", "tokens": 42, "prompt_tokens": 11,
        "latency_s": 104.7, "est_wh": 0.7271, "watts": 25, "class": "laptop", "src": "measured",
    })
    assert row is not None
    assert row["lung"] == "bob"
    assert row["latency_s"] == 104.7
    assert row["ts"] is not None
    assert row["tokens"] == 42.0


def test_parse_v1_row_rejects_garbage() -> None:
    assert fcl.parse_job_row({"schema": "fleet_cost_job.v1", "lung": "x", "latency_s": 0}) is None
    assert fcl.parse_job_row({"schema": "fleet_cost_job.v1", "lung": None, "latency_s": 5}) is None
    assert fcl.parse_job_row({"schema": "fleet_cost_job.v1", "latency_s": 5}) is None
    assert fcl.parse_job_row({"foo": 1}) is None
    assert fcl.parse_job_row("not a dict") is None


def test_parse_ollama_row() -> None:
    """Raw ollama /api/generate response: total_duration is nanoseconds."""
    row = fcl.parse_job_row({
        "model": "qwen2.5:3b", "total_duration": 104700000000,
        "eval_count": 42, "prompt_eval_count": 11,
    })
    assert row is not None
    assert row["latency_s"] == pytest.approx(104.7)
    assert row["tokens"] == 42.0
    assert row["prompt_tokens"] == 11.0
    assert row["src"] == "measured"
    assert fcl.parse_job_row({"model": "m", "total_duration": 0}) is None


def test_load_jobs_skips_bad_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "jobs.jsonl"
    good = {"schema": "fleet_cost_job.v1", "lung": "bob", "model": "m", "latency_s": 10.0, "tokens": 5}
    ledger.write_text(
        json.dumps(good) + "\n"
        + "{not json at all\n"
        + json.dumps({"foo": 1}) + "\n"
        + json.dumps(good) + "\n",
        encoding="utf-8")
    rows, skipped = fcl.load_jobs(ledger)
    assert len(rows) == 2
    assert skipped == 2
    assert fcl.load_jobs(tmp_path / "missing.jsonl") == ([], 0)


# ------------------------------------------------------------- heartbeats


CARD = {
    "kind": "mag_lung_introduction.v1",
    "generated": "2026-10-30T12:00:00Z",
    "hostname": "bob",
    "platform": "Linux 5.4 (x86_64)",
    "cpu_count": 4,
    "mem_total": "3.3G",
    "disk_free_gb": 80.0,
    "tailscale_ip": "100.64.0.1",
    "ollama": {"version": "0.3.4", "models": ["qwen2.5:3b"]},
    "generation": {"ok": True, "model": "qwen2.5:3b", "seconds": 104.7, "reply": "READY"},
}


def test_load_heartbeats_single_and_jsonl(tmp_path: Path) -> None:
    single = tmp_path / "card.json"
    single.write_text(json.dumps(CARD), encoding="utf-8")
    cards, warnings = fcl.load_heartbeats(single)
    assert len(cards) == 1 and cards[0]["hostname"] == "bob" and warnings == []

    jsonl = tmp_path / "cards.jsonl"
    jsonl.write_text(json.dumps(CARD) + "\n{oops\n", encoding="utf-8")
    cards2, warnings2 = fcl.load_heartbeats(jsonl)
    assert len(cards2) == 1
    assert len(warnings2) == 1

    cards3, warnings3 = fcl.load_heartbeats(tmp_path / "nope.json")
    assert cards3 == [] and len(warnings3) == 1


def test_heartbeat_probe_becomes_job_row() -> None:
    row = fcl.heartbeat_job_row(CARD)
    assert row is not None
    assert row["lung"] == "bob"
    assert row["latency_s"] == 104.7
    assert row["tokens"] == 0.0
    assert row["ts"] is not None
    assert fcl.heartbeat_job_row({**CARD, "generation": {"ok": False, "error": "ollama unreachable"}}) is None


# ------------------------------------------------------------- rollup math


def test_rollup_pure_standby() -> None:
    card = {**CARD, "generation": {"ok": False, "error": "skipped"}}
    rollup = fcl.build_rollup([card], [], window_days=30)
    lung = rollup["lungs"][0]
    assert lung["lung"] == "bob"
    assert lung["jobs"] == 0
    assert lung["duty"] == 0.0
    assert lung["cost_idle_floor_mo_usd"] == pytest.approx(0.54, abs=1e-6)
    assert lung["usd_per_m_full"] is None
    assert lung["tokens_mo_projected"] == 0.0
    assert any("PURE STANDBY" in v for v in lung["verdicts"])


def test_rollup_projection_at_ten_percent_duty() -> None:
    """Two rows spanning exactly 30d with 10% latency duty, no token counts.

    Laptop class (5/25W, 15 tok/s assumed): lane O cost $0.76 at 10% duty;
    projected yield 15 tok/s x 3600 x 720 x 0.1 = 3.888M tok/mo.
    """
    t0, t1 = "2026-10-01T00:00:00Z", "2026-10-31T00:00:00Z"
    half = 259200.0 / 2.0  # total latency 259200s = 10% of the 2592000s window
    rows = [
        fcl.parse_job_row({"schema": "fleet_cost_job.v1", "ts": t0, "lung": "bob",
                           "model": "qwen2.5:3b", "latency_s": half}),
        fcl.parse_job_row({"schema": "fleet_cost_job.v1", "ts": t1, "lung": "bob",
                           "model": "qwen2.5:3b", "latency_s": half}),
    ]
    rollup = fcl.build_rollup([], rows)
    lung = rollup["lungs"][0]
    assert lung["window_source"] == "job-span"
    assert lung["window_s"] == pytest.approx(2592000.0)
    assert lung["duty"] == pytest.approx(0.10, abs=1e-5)
    assert lung["tok_s_source"] == "class-assumed"
    assert lung["cost_total_mo_usd"] == pytest.approx(0.756, abs=1e-3)
    assert lung["tokens_mo_projected"] == pytest.approx(3_888_000.0, rel=1e-3)
    assert lung["usd_per_m_full"] == pytest.approx(0.756 / 3_888_000.0 * 1e6, rel=1e-3)
    assert lung["cost_marginal_mo_usd"] == pytest.approx(0.216, abs=1e-3)
    assert lung["usd_per_m_marginal"] == pytest.approx(0.0556, abs=1e-3)
    assert not any("LOW-DUTY" in v for v in lung["verdicts"])
    assert any("ALREADY-ON STRONG CASE" in v for v in lung["verdicts"])
    assert lung["est_wh_total"] == pytest.approx(25.0 * 259200.0 / 3600.0, rel=1e-3)


def test_rollup_measured_tok_s_and_low_duty_verdict() -> None:
    """A token-bearing row sets the measured rate; a tokenless row is estimated.

    rate = 100 tok / 10s = 10 tok/s measured; window clamps to the 1h minimum;
    duty 15/3600 = 0.417% triggers the lane C low-duty drowning verdict.
    """
    rows = [
        fcl.parse_job_row({"schema": "fleet_cost_job.v1", "ts": "2026-10-30T10:00:00Z",
                           "lung": "bob", "model": "m", "latency_s": 10.0, "tokens": 100}),
        fcl.parse_job_row({"schema": "fleet_cost_job.v1", "ts": "2026-10-30T10:05:00Z",
                           "lung": "bob", "model": "m", "latency_s": 5.0}),
    ]
    rollup = fcl.build_rollup([], rows)
    lung = rollup["lungs"][0]
    assert lung["tok_s_effective"] == pytest.approx(10.0)
    assert lung["tok_s_source"] == "measured"
    assert lung["tokens_measured"] == 100.0
    assert lung["tokens_estimated"] == pytest.approx(50.0)
    assert lung["window_source"] == "minimum-hour"
    assert lung["window_s"] == pytest.approx(3600.0)
    assert lung["duty"] == pytest.approx(15.0 / 3600.0, abs=1e-6)
    assert lung["tokens_mo_projected"] == pytest.approx(10.0 * 3600 * 720 * (15.0 / 3600.0), rel=1e-3)
    assert any("LOW-DUTY" in v for v in lung["verdicts"])


def test_rollup_class_and_watts_overrides() -> None:
    card = {**CARD, "generation": {"ok": False, "error": "skipped"}}
    rollup = fcl.build_rollup([card], [], class_map={"bob": "desktop"})
    lung = rollup["lungs"][0]
    assert lung["watts"]["idle"] == 60.0 and lung["watts"]["load"] == 180.0
    assert lung["cost_idle_floor_mo_usd"] == pytest.approx(6.48, abs=1e-6)

    rollup2 = fcl.build_rollup([card], [], watts_map={"bob": (4.0, 30.0)})
    lung2 = rollup2["lungs"][0]
    assert lung2["watts"]["idle"] == 4.0 and lung2["watts"]["load"] == 30.0
    assert lung2["cost_idle_floor_mo_usd"] == pytest.approx(4.0 * 720 / 1000 * 0.15, abs=1e-6)

    with pytest.raises(SystemExit):
        fcl._parse_watts(["bob=30,4"])  # idle must be below load
    assert fcl._parse_watts(["bob=4,30"]) == {"bob": (4.0, 30.0)}


def test_fleet_totals_sum_lungs() -> None:
    card_a = {**CARD}
    card_b = {**CARD, "hostname": "nova", "generation": {"ok": False, "error": "skipped"}}
    rollup = fcl.build_rollup([card_a, card_b], [], window_days=30)
    assert rollup["fleet"]["lungs"] == 2
    floors = sum(l["cost_idle_floor_mo_usd"] for l in rollup["lungs"])
    assert rollup["fleet"]["cost_idle_floor_mo_usd"] == pytest.approx(floors, abs=1e-6)


# ------------------------------------------------------------- record path


def test_build_record_row_validates() -> None:
    row = fcl.build_record_row("bob", "qwen2.5:3b", 104.7, None, None, "laptop", None, None)
    assert row["schema"] == "fleet_cost_job.v1"
    assert row["est_wh"] == pytest.approx(0.7271, abs=1e-3)
    assert row["src"] == "estimate"  # no tokens given
    with pytest.raises(ValueError):
        fcl.build_record_row("bob", "m", 0.0, None, None, "laptop", None, None)
    with pytest.raises(ValueError):
        fcl.build_record_row("", "m", 5.0, None, None, "laptop", None, None)
    with pytest.raises(ValueError):
        fcl.build_record_row("bob", "m", 5.0, -1, None, "laptop", None, None)


def test_record_round_trip(tmp_path: Path) -> None:
    ledger = tmp_path / "nested" / "jobs.jsonl"
    row = fcl.build_record_row("bob", "qwen2.5:3b", 104.7, 42.0, 11.0, "laptop",
                               "2026-10-30T12:00:00Z", "measured")
    fcl.append_row(row, ledger)
    rows, skipped = fcl.load_jobs(ledger)
    assert skipped == 0 and len(rows) == 1
    assert rows[0]["lung"] == "bob" and rows[0]["tokens"] == 42.0
    assert rows[0]["latency_s"] == pytest.approx(104.7)


def test_cli_record_from_ollama_needs_no_latency(tmp_path: Path) -> None:
    """Regression: --from-ollama supplies latency/tokens, --latency-s optional."""
    gen = tmp_path / "gen.json"
    gen.write_text(json.dumps({
        "model": "qwen2.5:3b", "total_duration": 104700000000,
        "eval_count": 33, "prompt_eval_count": 12}), encoding="utf-8")
    ledger = tmp_path / "jobs.jsonl"
    script = Path(ORGAN) / "fleet_cost_ledger.py"
    proc = subprocess.run(
        [sys.executable, str(script), "record", "--lung", "bob", "--model", "qwen2.5:3b",
         "--from-ollama", str(gen), "--class", "laptop", "--ledger", str(ledger)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    assert row["latency_s"] == pytest.approx(104.7)
    assert row["tokens"] == 33.0
    assert row["src"] == "measured"

    no_lat = subprocess.run(
        [sys.executable, str(script), "record", "--lung", "bob", "--model", "m",
         "--ledger", str(tmp_path / "none.jsonl")],
        capture_output=True, text=True, timeout=60)
    assert no_lat.returncode == 2
    assert "latency-s" in no_lat.stderr


# ------------------------------------------------------------- CLI smoke


def test_cli_project_json_smoke(tmp_path: Path) -> None:
    hb_dir = tmp_path / "cards"
    hb_dir.mkdir()
    (hb_dir / "bob.json").write_text(json.dumps(CARD), encoding="utf-8")
    script = Path(ORGAN) / "fleet_cost_ledger.py"
    proc = subprocess.run(
        [sys.executable, str(script), "project", "--heartbeats", str(hb_dir), "--json"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    rollup = json.loads(proc.stdout)
    assert rollup["schema"] == "fleet_cost_rollup.v1"
    assert rollup["lungs"][0]["lung"] == "bob"
    assert "gpt5-nano blended" in rollup["reference_lines_usd_per_m"]
    assert "breakeven_duty_by_line" in rollup["lungs"][0]


def test_cli_project_requires_input() -> None:
    script = Path(ORGAN) / "fleet_cost_ledger.py"
    proc = subprocess.run(
        [sys.executable, str(script), "project", "--jobs", "Z:/definitely/missing/jobs.jsonl"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert "nothing to ledger" in proc.stderr


def test_render_report_is_text_and_lists_breakevens() -> None:
    card = {**CARD}
    rollup = fcl.build_rollup([card], [], window_days=30)
    text = fcl.render_report(rollup)
    assert "FLEET COST LEDGER" in text
    assert "break-even duty" in text
    assert "HONESTY LIMITS" in text
    assert "smart plug" in text
    # no em-dashes anywhere in the render (estate law)
    assert "\u2014" not in text and "\u2013" not in text
