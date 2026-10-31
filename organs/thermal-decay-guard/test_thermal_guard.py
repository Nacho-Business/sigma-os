"""Verifiers for thermal_guard.py (organs/thermal-decay-guard): the blind-spot B4 throttling guard.

Fixtures are synthetic route1-style job ledgers in three health shapes (stable,
decaying, recovered) plus the real ledger's aliased row shape (endpoint ->
lung, output_tokens -> tokens, wall_end -> ts, ok=false rows skipped).
Covered: per-(lung, model) baseline/current tok/s math, the 0.60 threshold
verdicts (STABLE / THROTTLE_SUSPECTED / RECOVERED / INSUFFICIENT), rolling
episode detection over session order (ts sort, not file order), WARN/DEMOTE
rows appended to the actions JSONL (schema thermal_guard.v1), the --demote
block shape compatible with the tools/sigma_route_port.py emitted block
(4-space indent, evidence preamble, affinity: [embed, scut, router]), the
--report read-only fence, and exit codes 0/1/2.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

organ = str(Path(__file__).resolve().parent)
if organ not in sys.path:
    sys.path.insert(0, organ)

import thermal_guard as tguard  # noqa: E402


def run(argv: list[str], tmp_path: Path, actions: str | None = None) -> tuple[int, str]:
    """Run main() with stdout captured; --actions defaults into tmp_path."""
    if actions is not None and not any(a.startswith("--actions") for a in argv):
        argv = argv + ["--actions", str(tmp_path / actions)]
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = tguard.main(argv)
    return code, buf.getvalue()


def write_ledger(tmp_path: Path, rows: list[dict], name: str = "ledger.jsonl") -> str:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def canon_row(ts: int, tok_per_s: float, lung: str = "lungA", model: str = "gemma:2b",
              tokens: float = 100.0) -> dict:
    """Canonical shape row: latency derived so the job runs at tok_per_s."""
    return {"lung": lung, "model": model, "latency_s": tokens / tok_per_s,
            "tokens": tokens, "ts": ts}


def route1_row(ts: int, tok_per_s: float, tokens: float = 100.0, ok: bool = True,
               endpoint: str = "http://100.72.138.62:11434", model: str = "gemma:2b") -> dict:
    """The real results_bob.jsonl row shape (aliases, plus ok flag)."""
    latency = tokens / tok_per_s if tok_per_s else 999.0
    return {"endpoint": endpoint, "model": model, "id": f"route1-{ts}", "ok": ok,
            "latency_s": latency, "wall_start": float(ts), "wall_end": float(ts) + latency,
            "output_tokens": tokens, "tok_per_s_wall": tok_per_s, "err": "" if ok else "boom"}


def session(t0: int, rates: list[float], lung: str = "lungA",
            row_fn=canon_row) -> list[dict]:
    """Sequential jobs, one per second of ts, at the given per-job tok/s."""
    return [row_fn(t0 + i, r, lung=lung) if row_fn is canon_row else row_fn(t0 + i, r)
            for i, r in enumerate(rates)]


def read_actions(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# 1. Stable lung: flat tok/s stays STABLE, exit 0, no actions file written.
def test_stable_lung_verdict_stable_exit0_and_no_actions(tmp_path):
    ledger = write_ledger(tmp_path, session(1, [10.0] * 12))
    actions = tmp_path / "actions.jsonl"
    code, out = run(["--ledger", ledger], tmp_path, actions="actions.jsonl")
    assert code == 0
    assert "verdict=STABLE" in out
    assert "decay=0.0%" in out
    assert not actions.exists()


# 2. Decaying lung: 10 tok/s start, 3 tok/s tail => suspected, WARN row, exit 1.
def test_decaying_lung_suspected_warn_row_exit1(tmp_path):
    ledger = write_ledger(tmp_path, session(1, [10.0] * 6 + [3.0] * 6))
    actions = tmp_path / "actions.jsonl"
    code, out = run(["--ledger", ledger], tmp_path, actions="actions.jsonl")
    assert code == 1
    assert "verdict=THROTTLE_SUSPECTED" in out
    rows = read_actions(actions)
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "thermal_guard.v1"
    assert row["action"] == "WARN"
    assert row["lung"] == "lungA"
    assert row["baseline_tok_s"] == 10.0
    assert abs(row["current_tok_s"] - 3.0) < 1e-9
    assert row["threshold"] == 0.60


# 3. Recovered lung: fast, dipped mid-session, healthy tail => RECOVERED, exit 0.
def test_recovered_lung_recovered_exit0(tmp_path):
    ledger = write_ledger(tmp_path, session(1, [10.0] * 5 + [3.0] * 5 + [10.0] * 6))
    code, out = run(["--ledger", ledger], tmp_path, actions="actions.jsonl")
    assert code == 0
    assert "verdict=RECOVERED" in out
    assert "episodes=" in out
    assert "episodes=0" not in out  # the mid-session dip was seen


# 4. Too few usable rows: INSUFFICIENT, exit 0, no crash.
def test_insufficient_rows(tmp_path):
    ledger = write_ledger(tmp_path, session(1, [10.0] * 6))
    code, out = run(["--ledger", ledger], tmp_path, actions="actions.jsonl")
    assert code == 0
    assert "verdict=INSUFFICIENT" in out
    assert not (tmp_path / "actions.jsonl").exists()


# 5. The real route1 row shape parses through aliases; ok=false rows are skipped.
def test_route1_aliased_shape_and_failed_rows_skipped(tmp_path):
    rows = (session(1, [10.0] * 6, row_fn=route1_row)
            + [route1_row(100, 0.5, ok=False)]
            + session(200, [3.0] * 6, row_fn=route1_row))
    ledger = write_ledger(tmp_path, rows)
    actions = tmp_path / "actions.jsonl"
    code, out = run(["--ledger", ledger], tmp_path, actions="actions.jsonl")
    assert code == 1
    assert "verdict=THROTTLE_SUSPECTED" in out
    row = read_actions(actions)[0]
    assert row["lung"] == "http://100.72.138.62:11434"  # endpoint aliased to lung
    assert row["baseline_tok_s"] == 10.0
    assert abs(row["current_tok_s"] - 3.0) < 1e-9
    assert row["n"] == 12  # the ok=false row never entered the session


# 6. Threshold override: a mild decay to 0.70x only flags at --threshold 0.80.
def test_threshold_override_flags_mild_decay(tmp_path):
    rates = [10.0] * 6 + [7.0] * 6
    ledger = write_ledger(tmp_path, session(1, rates))
    code_default, out_default = run(["--ledger", ledger], tmp_path, actions="a1.jsonl")
    assert code_default == 0
    assert "verdict=STABLE" in out_default
    code_strict, out_strict = run(["--ledger", ledger, "--threshold", "0.80"],
                                  tmp_path, actions="a2.jsonl")
    assert code_strict == 1
    assert "verdict=THROTTLE_SUSPECTED" in out_strict


# 7. --demote block is sigma_route_port shaped: 4-space indent, evidence, affinity.
def test_demote_block_shape_sigma_compatible(tmp_path):
    ledger = write_ledger(tmp_path, session(1, [10.0] * 6 + [3.0] * 6))
    actions = tmp_path / "actions.jsonl"
    code, out = run(["--ledger", ledger, "--demote"], tmp_path, actions="actions.jsonl")
    assert code == 1
    assert "    # thermal_guard.v1 DEMOTED (" in out
    assert "    # Evidence (" in out and "ledger.jsonl" in out
    assert "    # baseline 10.000 tok/s (first 5 jobs) -> current 3.000 tok/s" in out
    assert "    # 70.0% below baseline; THROTTLE_SUSPECTED" in out
    assert "    affinity: [embed, scut, router]" in out
    rows = read_actions(actions)
    kinds = [r["action"] for r in rows]
    assert kinds == ["WARN", "DEMOTE"]
    note = rows[1]["note"]
    assert any("thermal_guard.v1 DEMOTED" in line for line in note)
    assert "    affinity: [embed, scut, router]" in note


# 8. --report prints health and writes nothing, even with a suspected lung.
def test_report_mode_writes_nothing(tmp_path):
    ledger = write_ledger(tmp_path, session(1, [10.0] * 6 + [3.0] * 6))
    code, out = run(["--ledger", ledger, "--report"], tmp_path, actions="actions.jsonl")
    assert code == 1  # report still carries the gate signal
    assert "baseline=10.000 tok/s current=3.000 tok/s decay=70.0%" in out
    assert "verdict=THROTTLE_SUSPECTED" in out
    assert not (tmp_path / "actions.jsonl").exists()


# 9. Exact math on hand-computed numbers: baseline 10.0, current 5.0, decay 50%.
def test_exact_baseline_window_math(tmp_path):
    rows = [canon_row(1, 10.0, tokens=100.0), canon_row(2, 10.0, tokens=100.0),
            canon_row(3, 5.0, tokens=50.0), canon_row(4, 5.0, tokens=50.0)]
    ledger = write_ledger(tmp_path, rows)
    code, out = run(["--ledger", ledger, "--baseline-jobs", "2", "--window", "2"],
                    tmp_path, actions="actions.jsonl")
    assert code == 1
    row = read_actions(tmp_path / "actions.jsonl")[0]
    assert row["baseline_tok_s"] == 10.0
    assert row["current_tok_s"] == 5.0
    assert row["ratio"] == 0.5
    assert abs(row["decay_pct"] - 50.0) < 1e-9
    assert "baseline=10.000 tok/s current=5.000 tok/s decay=50.0%" in out


# 10. Session order is ts order, not file order: a shuffled ledger still detects.
def test_ts_sorting_not_file_order(tmp_path):
    fast = [canon_row(ts, 10.0) for ts in (1, 2, 3, 4, 5)]
    slow = [canon_row(ts, 3.0) for ts in (6, 7, 8, 9, 10)]
    shuffled = slow + fast  # slow rows first on disk, but their ts is later
    ledger = write_ledger(tmp_path, shuffled)
    code, out = run(["--ledger", ledger], tmp_path, actions="actions.jsonl")
    assert code == 1
    assert "verdict=THROTTLE_SUSPECTED" in out


# 11. Invalid input: missing ledger and malformed JSON both exit 2.
def test_malformed_and_missing_ledger_exit2(tmp_path):
    code, out = run(["--ledger", str(tmp_path / "absent.jsonl")], tmp_path,
                    actions="actions.jsonl")
    assert code == 2
    assert "ledger not found" in out
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"lung": "a", "model": "m"\n', encoding="utf-8")
    code, out = run(["--ledger", str(bad)], tmp_path, actions="actions.jsonl")
    assert code == 2
    assert "malformed json" in out
    assert not (tmp_path / "actions.jsonl").exists()


# 12. Two lungs in one ledger: only the decayed one is flagged and acted on.
def test_two_lungs_only_decayed_flagged(tmp_path):
    rows = session(1, [10.0] * 12, lung="lungStable") + session(1, [10.0] * 6 + [3.0] * 6, lung="lungHot")
    ledger = write_ledger(tmp_path, rows)
    actions = tmp_path / "actions.jsonl"
    code, out = run(["--ledger", ledger], tmp_path, actions="actions.jsonl")
    assert code == 1
    by_line = {line.split()[0]: line for line in out.splitlines() if line.startswith("  lung=")}
    assert "verdict=STABLE" in by_line["lung=lungStable"]
    assert "verdict=THROTTLE_SUSPECTED" in by_line["lung=lungHot"]
    rows_out = read_actions(actions)
    assert len(rows_out) == 1
    assert rows_out[0]["lung"] == "lungHot"
