#!/usr/bin/env python
# prior_art: tools/cost_rollup.py, tools/hermes_burn.py (estate repo) -- cost_rollup reads the
#   five duel and mates cost schemas and hermes_burn banks one remote carrier's
#   usage; neither yields a per-lung fleet monthly cost and yield ledger.
"""fleet_cost_ledger.py - what the Mag lung swarm costs and yields, per month.

Consumer: run by the operator seat or any window on saved lung heartbeat cards
(organs/consent-gated-join/mag_introduce.py --json payloads, the same
artifacts organs/consent-gated-join/fleet_enroll.py consumes) plus the
job-row JSONL this tool itself writes with `record`. The SIGMA cockpit lane
(lane 06) is the named future consumer of the --json rollup
(fleet_cost_rollup.v1). Job rows may also come from any wrapper that emits the
minimal keys (lung, model, latency_s, tokens); organs/cost-priced/route1_cost.py
is that producer for the SIGMA-ROUTE-1 arm results (converts them, maps lung
via fleet/providers.yaml, appends here, and prints the experiment rollup), and
organs/consent-gated-join/fleet_enroll.py is the enrollment-proof producer
(ractor_engine 2026-10-30 repair step 3): a successful enrollment transcribes
the card's proof generation here (src=enroll-proof, laptop-class watts unless
--class says otherwise); refusals and no-ops append nothing.

Verifier: organs/cost-priced/test_fleet_cost_ledger.py (schema parse, rollup
math, break-even projection cross-checked against the lane tables, record
append, CLI smoke).

Split: the pure model (watts math, price lines, row grammar) lives in
cost_model.py (same directory); this file is rollup, record, render, CLI, and
re-exports the model surface so `import fleet_cost_ledger as fcl` keeps one
name for callers.

Stdlib only. No network: heartbeats are read from files, never probed live.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from cost_model import (  # noqa: F401  (re-exported model surface)
    CHARGER_LOSS_DEFAULT,
    DEVICE_CLASSES,
    JOB_SCHEMA,
    MIN_WINDOW_S,
    MONTH_HOURS_DEFAULT,
    REFERENCE_LINES,
    ROLLUP_SCHEMA,
    TARIFF_DEFAULT,
    WINDOW_DAYS_DEFAULT,
    breakeven_duty,
    est_wh,
    heartbeat_job_row,
    load_heartbeats,
    load_jobs,
    marginal_usd_per_m,
    monthly_cost,
    monthly_tokens,
    parse_job_row,
    parse_ts,
    render_report,
)

DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / "var" / "fleet" / "cost_jobs.jsonl"


# ---------------------------------------------------------------- rollup

def build_rollup(cards: list[dict], job_rows: list[dict], class_map: dict[str, str] | None = None,
                 watts_map: dict[str, tuple[float, float]] | None = None,
                 tariff: float = TARIFF_DEFAULT, month_hours: float = MONTH_HOURS_DEFAULT,
                 charger_loss: float = CHARGER_LOSS_DEFAULT,
                 window_days: float = WINDOW_DAYS_DEFAULT) -> dict:
    class_map = class_map or {}
    watts_map = watts_map or {}
    lungs: dict[str, dict] = {}

    def slot(name: str) -> dict:
        return lungs.setdefault(name, {"class": class_map.get(name, "laptop"), "rows": []})

    for card in cards:
        entry = slot(str(card.get("hostname")))
        row = heartbeat_job_row(card)
        if row is not None:
            entry["rows"].append(row)
    for row in job_rows:
        slot(row["lung"])["rows"].append(row)

    per_lung = []
    for name in sorted(lungs):
        entry = lungs[name]
        cls_name = entry["class"]
        cls = DEVICE_CLASSES.get(cls_name, DEVICE_CLASSES["laptop"])
        idle_w, load_w = cls["idle_w"], cls["load_w"]
        watts_note = None
        if name in watts_map:
            idle_w, load_w = watts_map[name]
            watts_note = "operator-override"
        rows = entry["rows"]
        latency_sum = sum(r["latency_s"] for r in rows)
        tokens_measured = sum(r["tokens"] for r in rows if r["tokens"] > 0)
        measured_lat = sum(r["latency_s"] for r in rows if r["tokens"] > 0)
        if tokens_measured > 0 and measured_lat > 0:
            tok_eff = tokens_measured / measured_lat
            tok_source = "measured"
        else:
            tok_eff = cls["tok_s"]
            tok_source = "class-assumed"
        tokens_estimated = sum(r["latency_s"] for r in rows if r["tokens"] <= 0) * tok_eff

        ts_list = [r["ts"] for r in rows if r["ts"] is not None]
        if len(ts_list) >= 2:
            span = (max(ts_list) - min(ts_list)).total_seconds()
            if span > 0:
                window_s = max(span, MIN_WINDOW_S)
                window_source = "job-span" if span >= MIN_WINDOW_S else "minimum-hour"
            else:
                window_s = window_days * 86400.0
                window_source = "assumed-days"
        else:
            window_s = window_days * 86400.0
            window_source = "assumed-days"
        duty = min(1.0, latency_sum / window_s) if window_s > 0 else 0.0

        cost_floor = monthly_cost(idle_w, idle_w, duty, month_hours, tariff, charger_loss)
        cost_total = monthly_cost(idle_w, load_w, duty, month_hours, tariff, charger_loss)
        cost_marginal = cost_total - cost_floor
        tokens_mo = monthly_tokens(tok_eff, duty, month_hours)
        usd_per_m_full = (cost_total / tokens_mo * 1e6) if tokens_mo > 0 else None
        usd_per_m_marginal = marginal_usd_per_m(idle_w, load_w, tok_eff, tariff, charger_loss)
        breakevens = {label: breakeven_duty(idle_w, load_w, tok_eff, price, month_hours, tariff)
                      for label, price in REFERENCE_LINES}

        verdicts: list[str] = []
        if latency_sum <= 0.0:
            verdicts.append("PURE STANDBY: no jobs in window; the idle floor is the whole bill")
        else:
            d_nano_out = breakevens.get("gpt5-nano output")
            if usd_per_m_full is not None and d_nano_out is not None and duty < d_nano_out:
                verdicts.append(
                    f"LOW-DUTY DROWN: at {duty * 100:.2f}% duty the idle floor puts full cost "
                    f"(${usd_per_m_full:.2f}/1M) above gpt5-nano output $0.40/1M (lane C case 1)")
        beaten = [label for label, price in REFERENCE_LINES if usd_per_m_marginal < price]
        if len(beaten) == len(REFERENCE_LINES):
            verdicts.append(
                f"ALREADY-ON STRONG CASE: marginal ${usd_per_m_marginal:.3f}/1M beats every listed "
                "2026 rung at any duty (lane C case 2)")
        elif beaten:
            verdicts.append(
                f"marginal ${usd_per_m_marginal:.3f}/1M beats: {', '.join(beaten)} at any duty")
        else:
            verdicts.append(
                f"marginal ${usd_per_m_marginal:.3f}/1M beats no listed rung (load-idle watts too fat "
                "for the token rate; meter this class before trusting it)")

        per_lung.append({
            "lung": name,
            "class": cls_name if watts_note is None else f"{cls_name} (watts: {watts_note})",
            "watts": {"idle": idle_w, "load": load_w},
            "tok_s_effective": round(tok_eff, 3),
            "tok_s_source": tok_source,
            "jobs": len(rows),
            "tokens_measured": tokens_measured,
            "tokens_estimated": tokens_estimated,
            "latency_s": round(latency_sum, 3),
            "est_wh_total": round(est_wh(load_w, latency_sum), 4),
            "window_s": round(window_s, 1),
            "window_source": window_source,
            "duty": round(duty, 6),
            "cost_idle_floor_mo_usd": round(cost_floor, 4),
            "cost_marginal_mo_usd": round(cost_marginal, 4),
            "cost_total_mo_usd": round(cost_total, 4),
            "tokens_mo_projected": round(tokens_mo, 1),
            "usd_per_m_full": None if usd_per_m_full is None else round(usd_per_m_full, 4),
            "usd_per_m_marginal": round(usd_per_m_marginal, 4),
            "breakeven_duty_by_line": {k: (None if v is None else round(v, 4)) for k, v in breakevens.items()},
            "verdicts": verdicts,
        })

    fleet_floor = sum(l["cost_idle_floor_mo_usd"] for l in per_lung)
    fleet_marginal = sum(l["cost_marginal_mo_usd"] for l in per_lung)
    fleet_total = sum(l["cost_total_mo_usd"] for l in per_lung)
    fleet_tokens = sum(l["tokens_mo_projected"] for l in per_lung)
    fleet = {
        "lungs": len(per_lung),
        "cost_idle_floor_mo_usd": round(fleet_floor, 4),
        "cost_marginal_mo_usd": round(fleet_marginal, 4),
        "cost_total_mo_usd": round(fleet_total, 4),
        "tokens_mo_projected": round(fleet_tokens, 1),
        "usd_per_m_full": None if fleet_tokens <= 0 else round(fleet_total / fleet_tokens * 1e6, 4),
    }
    return {
        "schema": ROLLUP_SCHEMA,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tariff_usd_kwh": tariff,
        "month_hours": month_hours,
        "charger_loss": charger_loss,
        "reference_lines_usd_per_m": {label: price for label, price in REFERENCE_LINES},
        "fleet": fleet,
        "lungs": per_lung,
    }


# ---------------------------------------------------------------- record

def build_record_row(lung: str, model: str, latency_s: float, tokens: float | None,
                     prompt_tokens: float | None, cls_name: str, ts: str | None,
                     src: str) -> dict:
    """Validate and shape one job row; raises ValueError on bad numbers."""
    if not lung:
        raise ValueError("lung is required")
    if not model:
        raise ValueError("model is required")
    latency_s = float(latency_s)
    if latency_s <= 0.0:
        raise ValueError(f"latency_s must be > 0, got {latency_s}")
    cls = DEVICE_CLASSES[cls_name]
    tokens_f = float(tokens) if tokens is not None else 0.0
    if tokens_f < 0.0:
        raise ValueError(f"tokens must be >= 0, got {tokens_f}")
    return {
        "schema": JOB_SCHEMA,
        "ts": ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lung": str(lung),
        "model": str(model),
        "tokens": tokens_f if tokens is not None else None,
        "prompt_tokens": float(prompt_tokens) if prompt_tokens is not None else None,
        "latency_s": round(latency_s, 3),
        "est_wh": round(est_wh(cls["load_w"], latency_s), 4),
        "watts": cls["load_w"],
        "class": cls_name,
        "src": src or ("measured" if tokens is not None else "estimate"),
    }


def append_row(row: dict, ledger_path: str | Path) -> None:
    p = Path(ledger_path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=True) + "\n")


# ---------------------------------------------------------------- CLI

def _parse_kv(pairs: list[str], flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"bad --{flag} {p!r}: want lung=value")
        lung, _, val = p.partition("=")
        out[lung.strip()] = val.strip()
    return out


def _parse_watts(pairs: list[str]) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for lung, val in _parse_kv(pairs, "watts").items():
        parts = val.split(",")
        if len(parts) != 2:
            raise SystemExit(f"bad --watts {lung}={val!r}: want idle_w,load_w")
        idle_w, load_w = float(parts[0]), float(parts[1])
        if not (0.0 < idle_w < load_w):
            raise SystemExit(f"bad --watts {lung}={val!r}: need 0 < idle_w < load_w")
        out[lung] = (idle_w, load_w)
    return out


def cmd_project(args: argparse.Namespace) -> int:
    if args.tariff <= 0 or args.month_hours <= 0 or args.charger_loss < 1.0:
        print("error: tariff and month-hours must be > 0, charger-loss >= 1.0", file=sys.stderr)
        return 2
    class_map = _parse_kv(args.class_pairs, "class")
    for lung, cls_name in class_map.items():
        if cls_name not in DEVICE_CLASSES:
            print(f"error: unknown class {cls_name!r} for lung {lung!r}; "
                  f"known: {', '.join(DEVICE_CLASSES)}", file=sys.stderr)
            return 2
    watts_map = _parse_watts(args.watts_pairs)

    cards: list[dict] = []
    warnings: list[str] = []
    for hb in args.heartbeats:
        sub_cards, sub_warn = load_heartbeats(hb)
        cards.extend(sub_cards)
        warnings.extend(sub_warn)
    rows, skipped = load_jobs(args.jobs)
    if not cards and not rows:
        print(f"error: nothing to ledger: no heartbeat cards and no job rows at {args.jobs}; "
              "pass --heartbeats (mag_introduce.py --json cards) or feed a job ledger", file=sys.stderr)
        return 2

    rollup = build_rollup(cards, rows, class_map, watts_map, args.tariff,
                          args.month_hours, args.charger_loss, args.window_days)
    rollup["sources"] = {
        "heartbeat_cards": len(cards),
        "heartbeat_warnings": warnings,
        "job_rows": len(rows),
        "job_rows_skipped": skipped,
        "jobs_path": args.jobs,
    }
    if args.json:
        print(json.dumps(rollup, indent=2))
    else:
        if warnings or skipped:
            print(f"(ingest notes: {len(cards)} cards, {len(rows)} job rows, "
                  f"{skipped} ledger lines skipped, warnings: {'; '.join(warnings) or 'none'})")
            print("")
        print(render_report(rollup))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    lung, model, latency_s, tokens, prompt_tokens = args.lung, args.model, args.latency_s, args.tokens, args.prompt_tokens
    src = args.src or "measured"
    if args.from_ollama:
        raw = json.loads(Path(args.from_ollama).read_text(encoding="utf-8"))
        latency_s = (raw.get("total_duration") or 0) / 1e9
        tokens = raw.get("eval_count")
        prompt_tokens = raw.get("prompt_eval_count")
        model = raw.get("model") or model
        if not latency_s:
            print(f"error: {args.from_ollama} has no usable total_duration", file=sys.stderr)
            return 2
        src = "measured"
    elif latency_s is None:
        print("error: --latency-s is required unless --from-ollama supplies it", file=sys.stderr)
        return 2
    try:
        row = build_record_row(lung, model, latency_s, tokens, prompt_tokens, args.cls, args.ts, src)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    append_row(row, args.ledger)
    print(json.dumps(row))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fleet cost ledger: monthly cost projection and token yield per Mag lung "
                    "(two-state power model, lane O; break-even vs cloud price lines, lane C). "
                    "Estimates, not measurements: see the report footer.",
        epilog="examples:  "
               "python organs/cost-priced/fleet_cost_ledger.py project --heartbeats var/fleet/cards/  |  "
               "python organs/cost-priced/fleet_cost_ledger.py record --lung bob --model qwen2.5:3b --latency-s 104.7")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_proj = sub.add_parser("project", help="project monthly cost and yield per lung from cards + job rows")
    p_proj.add_argument("--heartbeats", action="append", default=[],
                        help="heartbeat card file or directory (mag_introduce.py --json payload); repeatable")
    p_proj.add_argument("--jobs", default=str(DEFAULT_LEDGER),
                        help=f"job-ledger JSONL (default {DEFAULT_LEDGER}); missing file is fine")
    p_proj.add_argument("--class", dest="class_pairs", action="append", default=[],
                        help="lung=class device mapping (laptop|nuc|desktop|scavenger); repeatable")
    p_proj.add_argument("--watts", dest="watts_pairs", action="append", default=[],
                        help="lung=idle_w,load_w measured-watt override (the smart-plug path); repeatable")
    p_proj.add_argument("--tariff", type=float, default=TARIFF_DEFAULT, help="USD per kWh (default 0.15)")
    p_proj.add_argument("--month-hours", type=float, default=MONTH_HOURS_DEFAULT, help="hours per month (default 720)")
    p_proj.add_argument("--charger-loss", type=float, default=CHARGER_LOSS_DEFAULT,
                        help="charger/brick multiplier, 1.15 for ~15 percent brick loss (default 1.0)")
    p_proj.add_argument("--window-days", type=float, default=WINDOW_DAYS_DEFAULT,
                        help="duty window when job rows carry no usable timestamps (default 30)")
    p_proj.add_argument("--json", action="store_true", help="emit fleet_cost_rollup.v1 JSON (cockpit lane 06 form)")

    p_rec = sub.add_parser("record", help="append one job row to the ledger JSONL")
    p_rec.add_argument("--lung", required=True)
    p_rec.add_argument("--model", required=True)
    p_rec.add_argument("--latency-s", type=float, default=None,
                       help="seconds; required unless --from-ollama supplies it")
    p_rec.add_argument("--tokens", type=float, default=None, help="output tokens (eval_count); omit to estimate")
    p_rec.add_argument("--prompt-tokens", type=float, default=None)
    p_rec.add_argument("--class", dest="cls", default="laptop", choices=sorted(DEVICE_CLASSES))
    p_rec.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p_rec.add_argument("--from-ollama", default=None,
                       help="path to a raw ollama /api/generate response JSON; overrides the numeric args")
    p_rec.add_argument("--ts", default=None, help="ISO timestamp override (default now)")
    p_rec.add_argument("--src", default=None, help="row source tag (default measured|estimate)")

    args = ap.parse_args(argv)
    if args.cmd == "project":
        return cmd_project(args)
    return cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
