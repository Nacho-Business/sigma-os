#!/usr/bin/env python3
"""route1_cost (organs/cost-priced): wire SIGMA-ROUTE-1 result rows into the fleet cost ledger.

Consumer: the SIGMA wave 4 lane 14 operator window runs this on the route1 arm
result files (var/route1/results_*.jsonl written by organs/dispatch-exactly-once/route1_queue.py)
to append fleet_cost_job.v1 rows to logs/fleet_cost_jobs.jsonl - the same ledger
organs/cost-priced/fleet_cost_ledger.py project reads - and to print the per-lung experiment
rollup (tokens, Wh, $, $/1M) that lands in the lane receipt. This is the route1
producer the fleet_cost_ledger docstring anticipated: the ledger parser only
needs the minimal keys, this tool emits exactly those plus bounded trace fields
(route1_id, arm).

Verifier: tests/test_route1_cost.py (endpoint map parse, conversion math against
hand-computed Wh, rollup math, unknown-endpoint fail-closed handling, idempotent
append, CLI smoke).

Lung mapping: endpoint host:port is matched against the base_url rows of
configs/providers.yaml (ollama = 127.0.0.1:11434, ollama-bob-2 =
100.72.138.62:11434). An endpoint with no providers.yaml row is SKIPPED with a
warning, never guessed (fail-closed: a wrong lung would book watts to the wrong
power class).

Watts law (lane O two-state model, watch/inbox/SIGMA_BURN_LANE_O_POWER_20261030.md
and the estate route1 ledger (tools/route1_ledger.py, estate repo)): marginal inferring watts only, idle is sunk
(scavenger law). Desktop tower under CPU inference = 110 W wall = the fleet_cost_ledger
DEVICE_CLASSES "scavenger" load row; bob-class T470 laptop = 25 W = the "laptop"
load row. est_wh = load_w x latency_s / 3600, identical math to the banked
route1 ledger watt_s. Tariff default $0.15/kWh.

Rollup scope: EXPERIMENT cost (the generations actually run), not a monthly
projection - the monthly question is organs/cost-priced/fleet_cost_ledger.py project's job.

Stdlib only. No network: results are read from files, never probed live.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ORGAN_DIR = Path(__file__).resolve().parent
if str(ORGAN_DIR) not in sys.path:
    sys.path.insert(0, str(ORGAN_DIR))

import fleet_cost_ledger as fcl  # noqa: E402

REPO = ORGAN_DIR.parents[1]
DEFAULT_PROVIDERS = REPO / "fleet" / "providers.yaml"
DEFAULT_LEDGER = REPO / "var" / "fleet" / "cost_jobs.jsonl"
DEFAULT_RESULTS = [
    REPO / "var" / "route1" / "results_desktop_r2.jsonl",
    REPO / "var" / "route1" / "results_bob.jsonl",
    REPO / "var" / "route1" / "results_split.jsonl",
]
ROLLUP_SCHEMA = "route1_cost_rollup.v1"

# lane O class defaults per providers.yaml lung (see module docstring). A lung
# missing here fails closed: pass --class lung=class to map it explicitly.
DEFAULT_LUNG_CLASSES = {
    "ollama": "scavenger",      # desktop tower, 110 W CPU inference row
    "ollama-bob-2": "laptop",   # T470-class laptop, 25 W inference row
}

# providers.yaml is indentation-structured: provider keys sit at exactly two
# spaces, their base_url rows deeper inside the block. A tiny scanner beats a
# yaml dependency here (route1 tools are stdlib-only) and only two shapes matter.
PROVIDER_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$")
BASE_URL_RE = re.compile(r"^\s+base_url:\s*(\S+)")


def load_endpoint_map(providers_path: str | Path) -> dict[str, str]:
    """Map "host:port" from each provider's base_url row to the provider key."""
    endpoints: dict[str, str] = {}
    current: str | None = None
    text = Path(providers_path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = PROVIDER_RE.match(line)
        if m:
            current = m.group(1)
            continue
        m = BASE_URL_RE.match(line)
        if m and current:
            netloc = urlparse(m.group(1)).netloc
            if netloc:
                endpoints[netloc] = current
    return endpoints


def arm_name(path: str | Path) -> str:
    """results_desktop_r2.jsonl -> desktop_r2 (the arm tag carried in row src)."""
    stem = Path(path).stem
    return stem[len("results_"):] if stem.startswith("results_") else stem


def _iso_ts(wall_start) -> str | None:
    try:
        return datetime.fromtimestamp(float(wall_start), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def convert_row(row: dict, endpoint_map: dict[str, str], class_map: dict[str, str],
                arm: str) -> tuple[dict | None, str | None]:
    """One route1 result row -> (fleet_cost_job.v1 row, None) or (None, skip reason).

    Skips (never guesses): not-ok rows, endpoints absent from providers.yaml,
    lungs with no class mapping, and rows without a positive latency_s.
    """
    if not isinstance(row, dict):
        return None, "not-a-dict"
    if not row.get("ok"):
        return None, "not-ok"
    endpoint = str(row.get("endpoint") or "")
    netloc = urlparse(endpoint).netloc
    lung = endpoint_map.get(netloc)
    if lung is None:
        return None, f"unknown-endpoint:{endpoint or 'blank'}"
    cls_name = class_map.get(lung)
    if cls_name is None:
        return None, f"no-class-for-lung:{lung}"
    try:
        latency_s = float(row.get("latency_s") or 0.0)
    except (TypeError, ValueError):
        return None, "bad-latency"
    if latency_s <= 0.0:
        return None, "bad-latency"
    tokens = row.get("output_tokens", row.get("tokens"))
    try:
        job = fcl.build_record_row(lung, str(row.get("model") or "?"), latency_s,
                                   tokens, None, cls_name, _iso_ts(row.get("wall_start")),
                                   f"route1:{arm}")
    except ValueError as e:
        return None, f"bad-row:{e}"
    job["route1_id"] = row.get("id")
    job["arm"] = arm
    return job, None


def load_results(path: str | Path) -> tuple[list[dict], int]:
    """Read a route1 results JSONL; returns (rows, malformed_line_count)."""
    rows: list[dict] = []
    malformed = 0
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"results file not found: {p}")
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        rows.append(obj)
    return rows, malformed


def load_existing_keys(ledger_path: str | Path) -> set[tuple[str, object]]:
    """(src, route1_id) pairs already in the ledger, for idempotent re-runs."""
    keys: set[tuple[str, object]] = set()
    p = Path(ledger_path)
    if not p.exists():
        return keys
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("route1_id") is not None:
            keys.add((obj.get("src"), obj.get("route1_id")))
    return keys


# ---------------------------------------------------------------- rollup

def build_rollup(job_rows: list[dict], tariff: float) -> dict:
    """Experiment-scope rollup: per arm, per lung, fleet. Marginal watts only.

    $ = Wh / 1000 x tariff; $/1M = $ / tokens x 1e6. These are the actual
    generations run (lane O scavenger accounting), NOT a monthly projection.
    """
    def slot(bucket: dict, key: str) -> dict:
        return bucket.setdefault(key, {"jobs": 0, "tokens": 0.0, "latency_s": 0.0,
                                       "wh": 0.0})

    arms: dict[str, dict] = {}
    lungs: dict[str, dict] = {}
    for row in job_rows:
        arm_slot = slot(arms, str(row.get("arm") or "?"))
        lung_slot = slot(lungs, str(row["lung"]))
        for s in (arm_slot, lung_slot):
            s["jobs"] += 1
            s["tokens"] += float(row.get("tokens") or 0.0)
            s["latency_s"] += float(row["latency_s"])
            s["wh"] += float(row.get("est_wh") or 0.0)

    def finish(bucket: dict) -> list[dict]:
        out = []
        for name in sorted(bucket):
            s = bucket[name]
            cost = s["wh"] / 1000.0 * tariff
            s["cost_usd"] = cost
            s["usd_per_m"] = (cost / s["tokens"] * 1e6) if s["tokens"] > 0 else None
            s["latency_s"] = round(s["latency_s"], 3)
            s["wh"] = round(s["wh"], 4)
            s["cost_usd"] = round(cost, 6)
            s["usd_per_m"] = None if s["usd_per_m"] is None else round(s["usd_per_m"], 4)
            s["name"] = name
            out.append(s)
        return out

    per_arm = finish(arms)
    per_lung = finish(lungs)
    tot_tokens = sum(float(r.get("tokens") or 0.0) for r in job_rows)
    tot_wh = sum(float(r.get("est_wh") or 0.0) for r in job_rows)
    tot_lat = sum(float(r["latency_s"]) for r in job_rows)
    fleet_cost = tot_wh / 1000.0 * tariff
    fleet = {
        "jobs": len(job_rows),
        "tokens": tot_tokens,
        "latency_s": round(tot_lat, 3),
        "wh": round(tot_wh, 4),
        "cost_usd": round(fleet_cost, 6),
        "usd_per_m": round(fleet_cost / tot_tokens * 1e6, 4) if tot_tokens > 0 else None,
    }
    return {
        "schema": ROLLUP_SCHEMA,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tariff_usd_kwh": tariff,
        "arms": per_arm,
        "lungs": per_lung,
        "fleet": fleet,
    }


def render_report(rollup: dict, appended: int, already: int, skipped: dict[str, int],
                  sources: list[str]) -> str:
    lines = [
        f"ROUTE-1 COST WIRE (schema {rollup['schema']}, tariff ${rollup['tariff_usd_kwh']}/kWh, "
        f"generated {rollup['generated']})",
        "scope: the experiment's own generations; lane O marginal inferring watts only, "
        "idle is sunk (scavenger law)",
        "sources: " + ", ".join(sources),
        "",
    ]
    for arm in rollup["arms"]:
        lines.append(
            f"arm {arm['name']}: {arm['jobs']} jobs | {int(arm['tokens'])} tok | "
            f"{arm['latency_s']:.1f}s | {arm['wh']:.4f} Wh | ${arm['cost_usd']:.6f} | "
            f"{_fmt_pm(arm['usd_per_m'])}")
    lines.append("")
    lung_names = ", ".join(f"{l['name']}={l['jobs']}j" for l in rollup["lungs"]) or "none"
    lines.append(f"per lung ({lung_names}):")
    for lung in rollup["lungs"]:
        lines.append(
            f"  lung {lung['name']}: {lung['jobs']} jobs | {int(lung['tokens'])} tok | "
            f"{lung['latency_s']:.1f}s | {lung['wh']:.4f} Wh | ${lung['cost_usd']:.6f} | "
            f"{_fmt_pm(lung['usd_per_m'])}")
    f = rollup["fleet"]
    lines.append(
        f"FLEET (experiment): {f['jobs']} jobs | {int(f['tokens'])} tok | {f['latency_s']:.1f}s | "
        f"{f['wh']:.4f} Wh | ${f['cost_usd']:.6f} | {_fmt_pm(f['usd_per_m'])}")
    lines.append("")
    lines.append(f"ledger: {appended} rows appended, {already} already present (idempotent skip)")
    if skipped:
        lines.append("skipped: " + ", ".join(f"{k} x{n}" for k, n in sorted(skipped.items())))
    lines.append(
        "honesty: watts are lane O class rows (desktop 110 W, laptop 25 W), not wall "
        "measurements of these runs; charger loss excluded; prompt-eval watts inside "
        "latency are included, idle between jobs is not billed (marginal accounting).")
    return "\n".join(lines)


def _fmt_pm(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.4f}/1M"


# ---------------------------------------------------------------- CLI

def _parse_classes(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            print(f"error: bad --class {p!r}: want lung=class", file=sys.stderr)
            raise SystemExit(2)
        lung, _, cls_name = p.partition("=")
        cls_name = cls_name.strip()
        if cls_name not in fcl.DEVICE_CLASSES:
            print(f"error: bad --class {p!r}: unknown class {cls_name!r}; "
                  f"known: {', '.join(sorted(fcl.DEVICE_CLASSES))}", file=sys.stderr)
            raise SystemExit(2)
        out[lung.strip()] = cls_name
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Convert SIGMA-ROUTE-1 result rows into fleet_cost_job.v1 ledger rows "
                    "and print the per-lung experiment cost rollup (lane O two-state model).",
        epilog="example: .venv/Scripts/python.exe tools/route1_cost.py  "
               "(defaults to the three canon arm files, 90 generations)")
    ap.add_argument("--results", nargs="+", default=[str(p) for p in DEFAULT_RESULTS],
                    help="route1 results JSONL files (default: the canon A_r2/B/C arms)")
    ap.add_argument("--providers", default=str(DEFAULT_PROVIDERS),
                    help=f"providers.yaml for endpoint-to-lung mapping (default {DEFAULT_PROVIDERS})")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER),
                    help=f"fleet job ledger to append to (default {DEFAULT_LEDGER})")
    ap.add_argument("--class", dest="class_pairs", action="append", default=[],
                    help="lung=class device mapping override (scavenger|laptop|desktop|nuc); repeatable")
    ap.add_argument("--tariff", type=float, default=fcl.TARIFF_DEFAULT, help="USD per kWh (default 0.15)")
    ap.add_argument("--dry", action="store_true", help="convert and roll up but append nothing")
    ap.add_argument("--json", action="store_true", help="emit route1_cost_rollup.v1 JSON")
    args = ap.parse_args(argv)

    if args.tariff <= 0:
        print("error: tariff must be > 0", file=sys.stderr)
        return 2
    endpoint_map = load_endpoint_map(args.providers)
    if not endpoint_map:
        print(f"error: no base_url rows found in {args.providers}", file=sys.stderr)
        return 2
    class_map = dict(DEFAULT_LUNG_CLASSES)
    class_map.update(_parse_classes(args.class_pairs))

    job_rows: list[dict] = []
    skipped: dict[str, int] = {}
    existing = load_existing_keys(args.ledger)
    already = 0
    arm_files: list[tuple[str, Path]] = []
    for raw in args.results:
        p = Path(raw)
        if not p.is_absolute():
            p = Path.cwd() / p
        arm = arm_name(p)
        rows, malformed = load_results(p)
        if malformed:
            skipped[f"malformed-lines:{arm}"] = skipped.get(f"malformed-lines:{arm}", 0) + malformed
        arm_files.append((arm, p))
        for row in rows:
            job, reason = convert_row(row, endpoint_map, class_map, arm)
            if job is None:
                key = reason or "unknown"
                skipped[key] = skipped.get(key, 0) + 1
                continue
            if (job["src"], job.get("route1_id")) in existing:
                already += 1
                continue
            job_rows.append(job)

    if not job_rows and not already:
        print("error: nothing converted: every row was skipped or files empty; "
              "check --results and --providers", file=sys.stderr)
        return 2

    appended = 0
    if not args.dry:
        for job in job_rows:
            fcl.append_row(job, args.ledger)
        appended = len(job_rows)

    rollup = build_rollup(job_rows, args.tariff)
    rollup["appended"] = appended
    rollup["already_present"] = already
    rollup["skipped"] = dict(sorted(skipped.items()))
    rollup["sources"] = [str(p) for _, p in arm_files]
    if args.json:
        print(json.dumps(rollup, indent=1))
    else:
        print(render_report(rollup, appended, already, skipped, rollup["sources"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
