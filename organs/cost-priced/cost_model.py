"""cost_model - the two-state power model, price lines, and ledger parsing.

Split out of fleet_cost_ledger.py (SIGMA wave 4 lane 14, 2026-10-30) so each
file stays under the 400-line ratchet cap; the math and the row grammar are
unchanged. This module is the pure half: constants, dollars-and-watts math,
and the job-row/card parsers. fleet_cost_ledger.py is the I/O and CLI half.

Consumer: organs/cost-priced/fleet_cost_ledger.py (rollup, record, render,
CLI) and organs/consent-gated-join/fleet_enroll.py (build_record_row,
append_row, DEVICE_CLASSES via the fleet_cost_ledger re-exports).
Verifier: organs/cost-priced/test_fleet_cost_ledger.py (schema parse, rollup
math, break-even projection cross-checked against the lane tables, record
append, CLI smoke).

Power model: SIGMA lane O two-state laptop (5W idle / 25W inferring), month =
720h, tariff default $0.15/kWh (lane C band $0.15-0.17), class table from
watch/inbox/SIGMA_BURN_LANE_O_POWER_20261030.md and
watch/inbox/SIGMA_BURN_LANE_C_ECONOMICS_20261030.md. Duty cycle is ESTIMATED
from job latency sums over the observed window; it is not a wall measurement.
The real instrument is a $15 smart plug with energy monitoring per seat class.

Stdlib only. No network: heartbeats are read from files, never probed live.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

JOB_SCHEMA = "fleet_cost_job.v1"
ROLLUP_SCHEMA = "fleet_cost_rollup.v1"
MONTH_HOURS_DEFAULT = 720.0
TARIFF_DEFAULT = 0.15
CHARGER_LOSS_DEFAULT = 1.0
WINDOW_DAYS_DEFAULT = 30.0
MIN_WINDOW_S = 3600.0

# lane O anchor fleet (laptop 5/25) + lane C device table (nuc, desktop) and
# lane O scavenger frame (desktop idle 60, CPU inference ~110). tok_s are lane C
# yield-table assumptions; bob's measured qwen2.5:3b ~8 tok/s is the one
# calibration point (watch/inbox/BOB_RELIGHT_NIGHT_20261030.md).
DEVICE_CLASSES = {
    "laptop":    {"idle_w": 5.0,  "load_w": 25.0,  "tok_s": 15.0},
    "nuc":       {"idle_w": 8.0,  "load_w": 22.0,  "tok_s": 12.0},
    "desktop":   {"idle_w": 60.0, "load_w": 180.0, "tok_s": 25.0},
    "scavenger": {"idle_w": 60.0, "load_w": 110.0, "tok_s": 8.0},
}

# lane C cloud-comparison lines, per 1M tokens (2026 posted pricing).
REFERENCE_LINES = [
    ("gpt5-nano blended", 0.225),
    ("haiku5.5 blended", 0.30),
    ("gpt4o-mini blended", 0.375),
    ("gpt5-nano output", 0.40),
    ("glm-4.6 blended", 1.09),
]


# ---------------------------------------------------------------- pure math

def est_wh(load_w: float, latency_s: float) -> float:
    """Energy of one job under the two-state model: load W for latency seconds."""
    return load_w * latency_s / 3600.0


def monthly_cost(idle_w: float, load_w: float, duty: float, month_hours: float = MONTH_HOURS_DEFAULT,
                 tariff: float = TARIFF_DEFAULT, charger_loss: float = CHARGER_LOSS_DEFAULT) -> float:
    """Two-state monthly dollars: (idle + (load-idle)*duty) W x month x tariff."""
    watts = idle_w + (load_w - idle_w) * duty
    return watts * month_hours / 1000.0 * tariff * charger_loss


def monthly_tokens(tok_s: float, duty: float, month_hours: float = MONTH_HOURS_DEFAULT) -> float:
    """Projected output tokens per month at duty d and tok_s tokens/second."""
    return tok_s * 3600.0 * month_hours * duty


def breakeven_duty(idle_w: float, load_w: float, tok_s: float, price: float,
                   month_hours: float = MONTH_HOURS_DEFAULT, tariff: float = TARIFF_DEFAULT) -> float | None:
    """Duty d* where full monthly cost per 1M tokens equals `price`.

    Same algebra as lane C section (c) (their constants: 730h, $0.16/kWh).
    Returns None when the device never beats the price at any duty in [0,1]
    (denominator non-positive, like lane C's desktop rows, or d* > 1).
    """
    num = idle_w * month_hours * tariff / 1000.0
    den = price * tok_s * month_hours * 3600.0 / 1e6 - (load_w - idle_w) * month_hours * tariff / 1000.0
    if den <= 0.0:
        return None
    d = num / den
    if d > 1.0:
        return None
    return d


def marginal_usd_per_m(idle_w: float, load_w: float, tok_s: float,
                       tariff: float = TARIFF_DEFAULT, charger_loss: float = CHARGER_LOSS_DEFAULT) -> float:
    """$/1M tokens on marginal watts only (the already-on / scavenger case).

    Duty-independent: the idle floor is sunk, so only (load-idle) W x duty is
    billed against tok_s x duty x month tokens. Lane C's $0.075/1M laptop line
    is this function at their constants (idle 10, load 35, 15 tok/s, $0.16).
    """
    return (load_w - idle_w) * tariff * charger_loss / (tok_s * 3.6)


# ---------------------------------------------------------------- parsing

def parse_ts(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_job_row(obj) -> dict | None:
    """Normalize one JSONL row to the ledger shape, or None if not a job row.

    Accepts (1) fleet_cost_job.v1 rows written by `record` and (2) raw ollama
    /api/generate response shapes (eval_count, prompt_eval_count,
    total_duration in nanoseconds), so lungs can append keepalive/probe output
    directly to the ledger file.
    """
    if not isinstance(obj, dict):
        return None
    if obj.get("schema") == JOB_SCHEMA:
        lung = obj.get("lung")
        lat = obj.get("latency_s")
        if not lung or lat is None:
            return None
        try:
            lat = float(lat)
        except (TypeError, ValueError):
            return None
        if lat <= 0.0:
            return None
        return {
            "lung": str(lung),
            "model": str(obj.get("model") or "?"),
            "tokens": float(obj.get("tokens") or 0.0),
            "prompt_tokens": float(obj.get("prompt_tokens") or 0.0),
            "latency_s": lat,
            "est_wh": float(obj.get("est_wh") or 0.0),
            "ts": parse_ts(obj.get("ts")),
            "src": str(obj.get("src") or "measured"),
        }
    if "eval_count" in obj or "total_duration" in obj:
        lat = (obj.get("total_duration") or 0) / 1e9
        if lat <= 0.0:
            return None
        return {
            "lung": str(obj.get("lung") or "unknown"),
            "model": str(obj.get("model") or "?"),
            "tokens": float(obj.get("eval_count") or 0.0),
            "prompt_tokens": float(obj.get("prompt_eval_count") or 0.0),
            "latency_s": float(lat),
            "est_wh": 0.0,
            "ts": parse_ts(obj.get("ts")),
            "src": "measured",
        }
    return None


def load_jobs(path: str | Path) -> tuple[list[dict], int]:
    """Read a job-ledger JSONL file; returns (rows, skipped_line_count)."""
    rows: list[dict] = []
    skipped = 0
    p = Path(path)
    if not p.exists():
        return rows, skipped
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        row = parse_job_row(obj)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return rows, skipped


def load_heartbeats(path: str | Path) -> tuple[list[dict], list[str]]:
    """Read mag_introduce.py --json card(s) from a file or a directory of files.

    Accepts a single card JSON, a JSON array of cards, or JSONL of cards.
    Returns (cards, warnings); a card is anything with kind
    mag_lung_introduction.v1 and a hostname.
    """
    cards: list[dict] = []
    warnings: list[str] = []
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("*.json"))
        if not files:
            warnings.append(f"no *.json cards in {p}")
        for f in files:
            sub_cards, sub_warn = load_heartbeats(f)
            cards.extend(sub_cards)
            warnings.extend(sub_warn)
        return cards, warnings
    if not p.exists():
        warnings.append(f"heartbeat path missing: {p}")
        return cards, warnings
    text = p.read_text(encoding="utf-8", errors="replace")
    objs: list = []
    try:
        parsed = json.loads(text)
        objs = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                objs.append(json.loads(line))
            except json.JSONDecodeError:
                warnings.append(f"{p}: unparseable line skipped")
    for obj in objs:
        if isinstance(obj, dict) and obj.get("kind") == "mag_lung_introduction.v1" and obj.get("hostname"):
            cards.append(obj)
        else:
            warnings.append(f"{p}: not a mag_lung_introduction.v1 card, skipped")
    return cards, warnings


def heartbeat_job_row(card: dict) -> dict | None:
    """Turn a card's generation probe into a job row (model, seconds known).

    The probe reports no token count, so tokens stay 0 and the rollup estimates
    them at the lung's effective tok/s; the row is marked src=heartbeat-probe.
    """
    gen = card.get("generation") or {}
    if not gen.get("ok"):
        return None
    seconds = gen.get("seconds")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return None
    if seconds <= 0.0:
        return None
    return {
        "lung": str(card.get("hostname")),
        "model": str(gen.get("model") or "?"),
        "tokens": 0.0,
        "prompt_tokens": 0.0,
        "latency_s": seconds,
        "est_wh": 0.0,
        "ts": parse_ts(card.get("generated")),
        "src": "heartbeat-probe",
    }


# ---------------------------------------------------------------- render

def _fmt_usd(x: float) -> str:
    return f"${x:.2f}"


def _fmt_pm(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.2f}/1M"


def _fmt_duty(d: float | None) -> str:
    return "never" if d is None else f"{d * 100:.2f}%"


def render_report(rollup: dict) -> str:
    lines = [
        "FLEET COST LEDGER rollup "
        f"(tariff ${rollup['tariff_usd_kwh']}/kWh x charger-loss {rollup['charger_loss']:.2f}, "
        f"month {rollup['month_hours']:.0f}h, generated {rollup['generated']})",
        "price lines (lane C): "
        + ", ".join(f"{k} ${v}" for k, v in rollup["reference_lines_usd_per_m"].items()),
        "",
    ]
    for lung in rollup["lungs"]:
        w = lung["watts"]
        window_days = lung["window_s"] / 86400.0
        lines.append(
            f"lung {lung['lung']} [{lung['class']} {w['idle']:.0f}W/{w['load']:.0f}W] "
            f"tok/s {lung['tok_s_effective']} ({lung['tok_s_source']})")
        lines.append(
            f"  jobs {lung['jobs']} | latency {lung['latency_s']:.1f}s | duty {lung['duty'] * 100:.2f}% "
            f"(window {window_days:.1f}d {lung['window_source']}) | {lung['est_wh_total']:.2f} Wh in window")
        lines.append(
            f"  tokens: {int(lung['tokens_measured'])} measured + {int(lung['tokens_estimated'])} estimated "
            f"| yield {int(lung['tokens_mo_projected'])} tok/mo projected")
        lines.append(
            f"  monthly: floor {_fmt_usd(lung['cost_idle_floor_mo_usd'])} (sunk idle) "
            f"+ marginal {_fmt_usd(lung['cost_marginal_mo_usd'])} "
            f"= {_fmt_usd(lung['cost_total_mo_usd'])} | $/1M full {_fmt_pm(lung['usd_per_m_full'])} "
            f"| marginal ${lung['usd_per_m_marginal']:.3f}/1M")
        be = ", ".join(f"{label} {_fmt_duty(d)}" for label, d in lung["breakeven_duty_by_line"].items())
        lines.append(f"  break-even duty: {be}")
        for v in lung["verdicts"]:
            lines.append(f"  verdict: {v}")
        lines.append("")
    f = rollup["fleet"]
    lines.append(
        f"FLEET: {f['lungs']} lungs | floor {_fmt_usd(f['cost_idle_floor_mo_usd'])}/mo "
        f"+ marginal {_fmt_usd(f['cost_marginal_mo_usd'])}/mo = {_fmt_usd(f['cost_total_mo_usd'])}/mo "
        f"| {int(f['tokens_mo_projected'])} tok/mo | full {_fmt_pm(f['usd_per_m_full'])}")
    lines.append("")
    lines.append("HONESTY LIMITS (read before quoting any number above):")
    lines.append("- watts are class surveys (NotebookCheck T470 rows: 4-6.8W idle / 34W load; lane C")
    lines.append("  NUC and desktop rows), not wall measurements of these specific boxes.")
    lines.append("- duty is ESTIMATED from job latency sums over the window (API-call time only).")
    lines.append("  Model loads, prompt eval and gaps between calls are not metered, so electrical")
    lines.append("  duty is plausibly HIGHER than shown; the idle floor dominates the bill either way.")
    lines.append("- charger brick loss (about 15%) is excluded unless you pass --charger-loss 1.15.")
    lines.append("- tokens from rows without counts are ESTIMATED at the class tok/s (lane C yield-table")
    lines.append("  assumptions); bob's measured ~8 tok/s on qwen2.5:3b is the only calibration point.")
    lines.append("- the real instrument is a $15 smart plug with energy monitoring per seat class.")
    lines.append("  Meter one seat for a week, then pin the measured pair with --watts lung=idle,load.")
    return "\n".join(lines)
