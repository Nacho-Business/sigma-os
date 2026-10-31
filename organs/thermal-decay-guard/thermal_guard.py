#!/usr/bin/env python3
"""thermal_guard: detect thermal/CPU throttling tok/s decay from a route1-style job ledger.

Blind spot B4 (SIGMA wave 4 lane 06): a throttling laptop shows tok/s decay over
sustained load; undetected, the fleet keeps routing jobs into a slowed node.

Consumer: the fleet routing seat and the SIGMA wave 4 operator window; the actions
JSONL this tool appends (schema thermal_guard.v1) is the tripwire a cron/gate can
read, and the --demote block is spliced by hand under a node's configs/providers.yaml
row exactly like the tools/sigma_route_port.py emitted FALSIFIED block (comment
preamble naming its evidence + a 4-space-indented affinity: line).
Verifier: tests/test_thermal_guard.py (stable, decaying, and recovered fixtures on
the canonical and route1-aliased row shapes; demote block shape; report read-only
fence; exit codes).

Input: a rolling job-latency history JSONL. Canonical row keys: lung, model,
latency_s, tokens, ts. The real route1 ledger shape (tools/route1_queue.py --out,
e.g. memory/runs/route1/results_bob.jsonl) is accepted through aliases:
endpoint -> lung, output_tokens -> tokens, wall_end -> ts; rows with ok=false or
non-positive latency/tokens are skipped, and a missing ts falls back to file order.

Detection: per (lung, model), sorted by ts. Baseline = aggregate tok/s
(sum tokens / sum latency_s) over the first --baseline-jobs jobs of the session.
The current window is the last --window jobs; --window-sized windows also roll
(one-job stride) over the post-baseline span to find decay episodes.
current tok/s < --threshold (default 0.60) x baseline => THROTTLE_SUSPECTED.
An episode earlier in the session with a healthy final window => RECOVERED.
Otherwise STABLE; too few usable rows => INSUFFICIENT.

Modes: default evaluates and appends WARN rows (and DEMOTE rows under --demote,
which also prints the demotion block to stdout) to --actions. --report prints
per-lung health and writes nothing. Exit codes: 0 clean/recovered/insufficient,
1 when any lung+model is THROTTLE_SUSPECTED (gate/cron signal), 2 invalid input
(missing ledger, malformed JSON, bad args). Stdlib only. Repo venv:
.venv/Scripts/python.exe tools/thermal_guard.py --ledger <jsonl> [--report|--demote]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

SCHEMA = "thermal_guard.v1"
DEMOTE_AFFINITY = ["embed", "scut", "router"]
DEFAULT_BASELINE_JOBS = 5
DEFAULT_WINDOW = 5
DEFAULT_THRESHOLD = 0.60

STABLE = "STABLE"
SUSPECTED = "THROTTLE_SUSPECTED"
RECOVERED = "RECOVERED"
INSUFFICIENT = "INSUFFICIENT"

ROOT = Path(__file__).resolve().parents[2]


class GuardError(Exception):
    """Invalid input (missing ledger, malformed JSON): main turns this into exit 2."""


def _num(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"not a number: {value!r}")
    return float(value)


def norm_row(raw: dict, idx: int) -> dict | None:
    """Canonical row from one ledger line; None means skip (failed or unusable row)."""
    if raw.get("ok") is False:
        return None
    try:
        latency = _num(raw.get("latency_s"))
        tokens = _num(raw.get("tokens", raw.get("output_tokens")))
    except (TypeError, ValueError):
        return None
    if latency <= 0 or tokens <= 0:
        return None
    lung = raw.get("lung") or raw.get("endpoint") or raw.get("node") or "unknown"
    model = raw.get("model") or "unknown"
    ts = raw.get("ts", raw.get("wall_end", raw.get("timestamp")))
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        ts = float(idx)  # no clock in the row: file order is the session order
    return {"lung": str(lung), "model": str(model), "latency_s": latency,
            "tokens": tokens, "ts": float(ts), "idx": idx}


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise GuardError(f"ledger not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise GuardError(f"cannot read ledger {path}: {e}")
    rows: list[dict] = []
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise GuardError(f"{path}:{ln}: malformed json: {e}")
        if not isinstance(raw, dict):
            raise GuardError(f"{path}:{ln}: row is not a json object")
        row = norm_row(raw, len(rows))
        if row is not None:
            rows.append(row)
    return rows


def tok_s(rows: list[dict]) -> float:
    """Aggregate tok/s over a slice: sum(tokens) / sum(latency_s)."""
    latency = sum(r["latency_s"] for r in rows)
    tokens = sum(r["tokens"] for r in rows)
    return tokens / latency if latency > 0 else 0.0


def analyze(rows: list[dict], baseline_jobs: int, window: int, threshold: float) -> list[dict]:
    """Per (lung, model) health. Pure: no I/O, no clock."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["lung"], r["model"]), []).append(r)
    out: list[dict] = []
    for (lung, model), jobs in sorted(groups.items()):
        jobs.sort(key=lambda r: (r["ts"], r["idx"]))
        n = len(jobs)
        health = {"lung": lung, "model": model, "n": n, "episodes": 0}
        if n < baseline_jobs + window:
            out.append({**health, "baseline_tok_s": None, "current_tok_s": None,
                        "ratio": None, "decay_pct": None, "verdict": INSUFFICIENT})
            continue
        baseline = tok_s(jobs[:baseline_jobs])
        current = tok_s(jobs[-window:])
        episodes = sum(1 for i in range(baseline_jobs, n - window + 1)
                       if tok_s(jobs[i:i + window]) < threshold * baseline)
        ratio = current / baseline if baseline > 0 else None
        decay_pct = (1.0 - ratio) * 100.0 if ratio is not None else None
        if ratio is not None and current < threshold * baseline:
            verdict = SUSPECTED
        elif episodes:
            verdict = RECOVERED
        else:
            verdict = STABLE
        out.append({**health, "baseline_tok_s": baseline, "current_tok_s": current,
                    "ratio": ratio, "decay_pct": decay_pct, "verdict": verdict,
                    "episodes": episodes})
    return out


def print_report(healths: list[dict], ledger: Path) -> None:
    print(f"thermal_guard {SCHEMA} report: ledger {ledger}")
    for h in healths:
        if h["baseline_tok_s"] is None:
            print(f"  lung={h['lung']} model={h['model']} n={h['n']} "
                  f"verdict={h['verdict']} (need baseline_jobs+window usable rows)")
            continue
        print(f"  lung={h['lung']} model={h['model']} n={h['n']} "
              f"baseline={h['baseline_tok_s']:.3f} tok/s current={h['current_tok_s']:.3f} tok/s "
              f"decay={h['decay_pct']:.1f}% episodes={h['episodes']} verdict={h['verdict']}")


def demote_lines(h: dict, ledger_disp: str, date: str, threshold: float,
                 baseline_jobs: int, window: int) -> list[str]:
    """Demotion note in the sigma_route_port emitted-block shape: 4-space indent,
    comment preamble naming the ledger evidence, then the affinity line."""
    return [
        f"    # {SCHEMA} DEMOTED ({date}): sustained tok/s decay on this node, "
        f"guard threshold {threshold:.2f}.",
        f"    # Evidence ({ledger_disp}): lung {h['lung']} model {h['model']}:",
        f"    # baseline {h['baseline_tok_s']:.3f} tok/s (first {baseline_jobs} jobs) -> "
        f"current {h['current_tok_s']:.3f} tok/s (last {window} jobs),",
        f"    # {h['decay_pct']:.1f}% below baseline; {SUSPECTED} "
        f"({h['episodes']} rolling window(s) under threshold).",
        f"    # Consequence in force: demote to {', '.join(DEMOTE_AFFINITY)} on the "
        f"local-generation axis until a cool re-probe clears the guard.",
        f"    affinity: [{', '.join(DEMOTE_AFFINITY)}]",
    ]


def action_row(h: dict, action: str, ledger_disp: str, threshold: float,
               baseline_jobs: int, window: int, note: list[str] | None = None) -> dict:
    row = {"schema": SCHEMA, "ts": time.time(), "date": dt.date.today().isoformat(),
           "action": action, "lung": h["lung"], "model": h["model"], "n": h["n"],
           "baseline_tok_s": h["baseline_tok_s"], "current_tok_s": h["current_tok_s"],
           "decay_pct": h["decay_pct"], "ratio": h["ratio"], "episodes": h["episodes"],
           "threshold": threshold, "baseline_jobs": baseline_jobs, "window": window,
           "ledger": ledger_disp}
    if note is not None:
        row["note"] = note
    return row


def append_actions(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def disp(path: Path) -> str:
    """Repo-relative posix when under ROOT, else absolute (sigma_route_port pattern)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Detect tok/s throttling decay in a route1-style job ledger")
    ap.add_argument("--ledger", required=True,
                    help="job history JSONL (route1 results shape or canonical lung/tokens/ts)")
    ap.add_argument("--actions", default="logs/thermal_actions.jsonl",
                    help="WARN/DEMOTE JSONL sink (appended, never rewritten)")
    ap.add_argument("--baseline-jobs", type=int, default=DEFAULT_BASELINE_JOBS,
                    help="early-session jobs forming the baseline tok/s (default 5)")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                    help="rolling/current window size in jobs (default 5)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="current/baseline ratio under which a lung is suspected (default 0.60)")
    ap.add_argument("--report", action="store_true",
                    help="print per-lung health and write nothing")
    ap.add_argument("--demote", action="store_true",
                    help="also emit the sigma_route_port-shaped demotion block for suspected nodes")
    args = ap.parse_args(argv)
    if args.baseline_jobs < 1 or args.window < 1:
        print("thermal_guard: exit 2: --baseline-jobs and --window must be >= 1")
        return 2
    ledger = Path(args.ledger)
    try:
        rows = load_rows(ledger)
    except GuardError as e:
        print(f"thermal_guard: exit 2: {e}")
        return 2
    healths = analyze(rows, args.baseline_jobs, args.window, args.threshold)
    print_report(healths, ledger)
    bad = [h for h in healths if h["verdict"] == SUSPECTED]
    if args.report:
        return 1 if bad else 0
    ledger_disp = disp(ledger)
    date = dt.date.today().isoformat()
    pending_actions: list[dict] = []
    for h in bad:
        note = None
        if args.demote:
            note = demote_lines(h, ledger_disp, date, args.threshold,
                                args.baseline_jobs, args.window)
            print(f"# demotion block for lung {h['lung']} "
                  f"(splice under its providers.yaml row, sigma_route_port shape):")
            for line in note:
                print(line)
        pending_actions.append(action_row(h, "WARN", ledger_disp, args.threshold,
                                          args.baseline_jobs, args.window))
        if note is not None:
            pending_actions.append(action_row(h, "DEMOTE", ledger_disp, args.threshold,
                                              args.baseline_jobs, args.window, note=note))
    if pending_actions:
        append_actions(Path(args.actions), pending_actions)
    if bad:
        print(f"thermal_guard: {len(bad)} lung(s) THROTTLE_SUSPECTED; "
              f"actions appended to {args.actions}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
