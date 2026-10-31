#!/usr/bin/env python
"""fleet_heartbeat_collect.py - the coordinator-side fleet heartbeat collector.

Consumer: the cockpit fleet feed (SIGMA_MAP_06_COCKPIT_20261030.md section 3a:
memory/runs/sigma_fleet/fleet_view.json, one row per lung, read by the
dashboard /api/v1/sigma/fleet door when it lands) and the lane T G6 dark rule
that keys every coordinator escalation off last_seen age (SIGMA_MAP_08_HEALTH
_20261030.md section 2: "the coordinator plane is 100 percent spec and zero
tools"; this collector is the first tool). SIGMA wave 2 lane 13, 2026-10-30.
Verifier: tests/test_fleet_heartbeat_collect.py (fixture cards fresh/stale/
dark/malformed, collector state transitions, jsonl append idempotency, ledger
prune, fleet view shape).

Stdlib only, so the coordinator seat runs it pre-venv. Inputs, two shapes:
  --cards PATH [PATH ...]  cards already landed as JSON files, directories
                           (every *.json inside) or glob patterns
  --fleet-config PATH      live pulls over ssh; JSON mapping lung name to an
                           ssh alias (string) or {"ssh_alias", "remote_root"};
                           runs ssh <alias> "cd <root> && python3 organs/consent-gated-join/mag_introduce.py --json"

Every accepted card appends one ledger row {"received_at","host","card"} to
memory/runs/sigma_fleet/heartbeats.jsonl. Appends are idempotent on
(host.lower(), card.generated): the same card re-fed never double-appends.
Malformed payloads are skipped, counted, never appended.

The fleet view reads the ledger, takes the latest card per hostname and
derives per-lung state from age:
  alive <= ALIVE_GRACE_MIN (15 min beat + jitter grace, 20 min)
  warn  between grace and dark
  DARK  at DARK_AFTER_MIN = BEAT_MINUTES x DARK_BEATS = 45 min (lane T (a) G6,
        3 missed beats)
  proof stale past PROOF_STALE_MIN = 150 min (hourly full probe cadence plus
        margin, lane T (f))
Lungs named in the fleet config but absent from the ledger render dark with
last_seen null ("never seen": never_seen -> alive is the collector's first
state transition). The ledger prunes itself at RETENTION_DAYS = 7 (lane T (f)
retention bound, the 429G law: every new surface names its own bound).
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = REPO / "var" / "fleet" / "heartbeats.jsonl"
DEFAULT_VIEW = REPO / "var" / "fleet" / "fleet_view.json"

CARD_KIND = "mag_lung_introduction.v1"
BEAT_MINUTES = 15                            # lane T (f): the cheap --no-probe beat cadence
DARK_BEATS = 3                               # lane T (a) G6: dark at 3 missed beats
DARK_AFTER_MIN = BEAT_MINUTES * DARK_BEATS   # 45
ALIVE_GRACE_MIN = 20                         # beat + jitter grace, cockpit lane 06 table
PROOF_STALE_MIN = 150                        # hourly full probe; proof older than ~2.5h = stale
RETENTION_DAYS = 7                           # lane T (f) heartbeat retention bound
SSH_TIMEOUT_S = 600                          # generation probe on a small CPU box took 104s once
DEFAULT_REMOTE_ROOT = "~/Documents/projects/sigma-os"


def parse_iso(ts: object) -> datetime.datetime | None:
    """Parse a card 'generated' stamp (UTC, Z-suffixed or naive ISO)."""
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip()
    try:
        if raw.endswith("Z"):
            return datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def iso_utc(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_card(obj: object) -> tuple[bool, str]:
    """Accept anything a --no-probe beat or a full probe can emit (lane T (f)):
    kind, hostname and a parseable generated stamp. Probe absence is fine; a
    card is the carrier, not the proof."""
    if not isinstance(obj, dict):
        return False, "payload is not a JSON object"
    if obj.get("kind") != CARD_KIND:
        return False, f"kind is not {CARD_KIND!r}"
    host = obj.get("hostname")
    if not isinstance(host, str) or not host.strip():
        return False, "hostname missing or empty"
    if parse_iso(obj.get("generated") or "") is None:
        return False, f"generated stamp unparseable: {obj.get('generated')!r}"
    return True, "ok"


def load_cards_from_args(paths: list[str]) -> tuple[list[dict], list[str]]:
    """Expand files / directories / glob patterns into card payloads. Returns
    (cards, rejects); a bad file is one reject string, never a crash."""
    files: list[str] = []
    for p in paths:
        matches = sorted(glob.glob(p)) or ([p] if Path(p).is_file() else [])
        for m in matches:
            if Path(m).is_dir():
                files.extend(sorted(str(x) for x in Path(m).glob("*.json")))
            else:
                files.append(m)
    cards: list[dict] = []
    rejects: list[str] = []
    for f in files:
        try:
            obj = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            rejects.append(f"{f}: unreadable or invalid JSON ({type(e).__name__})")
            continue
        ok, reason = validate_card(obj)
        (cards.append(obj) if ok else rejects.append(f"{f}: {reason}"))
    return cards, rejects


def read_ledger(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    rows: list[dict] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a torn tail line never takes the collector down
    return rows


def ledger_keys(rows: list[dict]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for r in rows:
        card = r.get("card") or {}
        host, gen = r.get("host") or card.get("hostname") or "", card.get("generated") or ""
        if host and gen:
            keys.add((host.lower(), gen))
    return keys


def append_cards(cards: list[dict], ledger: Path, now: datetime.datetime | None = None) -> tuple[int, int]:
    """Append one row per card, skipping cards already banked. Idempotency key
    is (host.lower(), generated): the same card re-fed lands once. Returns
    (appended, skipped)."""
    if not cards:
        return 0, 0
    now = now or datetime.datetime.now(datetime.timezone.utc)
    seen = ledger_keys(read_ledger(ledger))
    new_rows: list[dict] = []
    for card in cards:
        key = ((card.get("hostname") or "").lower(), card.get("generated") or "")
        if key in seen:
            continue
        seen.add(key)
        new_rows.append({"received_at": iso_utc(now), "host": card.get("hostname"), "card": card})
    if not new_rows:
        return 0, len(cards)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        for row in new_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new_rows), len(cards) - len(new_rows)


def prune_ledger(ledger: Path, days: int = RETENTION_DAYS, now: datetime.datetime | None = None) -> int:
    """Drop ledger rows received older than the retention bound (lane T (f)).
    Returns rows removed; a fully current ledger is never rewritten."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows = read_ledger(ledger)
    cutoff = now - datetime.timedelta(days=days)
    kept: list[dict] = []
    for r in rows:
        stamp = parse_iso(r.get("received_at") or (r.get("card") or {}).get("generated") or "")
        (kept.append(r) if stamp is None or stamp >= cutoff else None)
    removed = len(rows) - len(kept)
    if not removed:
        return 0
    if not kept:
        ledger.unlink()
        return removed
    tmp = ledger.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(ledger)
    return removed


def status_for(age_min: float | None) -> str:
    """G6 dark rule: alive within the grace window, dark at 3 missed beats."""
    if age_min is None:
        return "dark"
    if age_min <= ALIVE_GRACE_MIN:
        return "alive"
    return "warn" if age_min < DARK_AFTER_MIN else "dark"


def lung_row(host: str, card: dict | None, received_at: str | None, now: datetime.datetime) -> dict:
    """One cockpit feed row per lung (SIGMA_MAP_06 section 3a columns)."""
    if card is None:
        return {"host": host, "status": "dark", "last_seen": None, "last_seen_age_min": None,
                "models": [], "model_count": 0, "ollama_version": None, "disk_free_gb": None,
                "mem_total": None, "tailscale_ip": None, "proof": None, "received_at": None,
                "note": "never seen"}
    generated = parse_iso(card.get("generated") or "")
    age_min = round((now - generated).total_seconds() / 60.0, 1) if generated else None
    o = card.get("ollama") or {}
    models = [m for m in (o.get("models") or []) if m]
    gen = card.get("generation") or {}
    proof: dict | None = None
    if isinstance(gen, dict) and gen:
        # the probe ran at card generation time, so proof age = card age
        proof = {"ok": bool(gen.get("ok")), "model": gen.get("model"), "seconds": gen.get("seconds"),
                 "age_min": age_min, "stale": bool(age_min is not None and age_min > PROOF_STALE_MIN)}
    return {"host": host, "status": status_for(age_min), "last_seen": card.get("generated"),
            "last_seen_age_min": age_min, "models": models, "model_count": len(models),
            "ollama_version": o.get("version"), "disk_free_gb": card.get("disk_free_gb"),
            "mem_total": card.get("mem_total"), "tailscale_ip": card.get("tailscale_ip"),
            "proof": proof, "received_at": received_at}


def build_fleet_view(ledger: Path, now: datetime.datetime | None = None,
                     configured_lungs: list[str] | None = None) -> dict:
    """Latest card per hostname, one feed row per lung, G6 dark rule applied.
    Lungs named in the fleet config but never heard from render dark."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    latest: dict[str, tuple[datetime.datetime, dict]] = {}
    for r in read_ledger(ledger):
        card = r.get("card") or {}
        host = r.get("host") or card.get("hostname") or ""
        stamp = parse_iso(card.get("generated") or "")
        if not host or stamp is None:
            continue
        cur = latest.get(host.lower())
        if cur is None or stamp > cur[0]:
            latest[host.lower()] = (stamp, r)
    lungs: list[dict] = []
    for key, (_stamp, r) in latest.items():
        card = r.get("card") or {}
        lungs.append(lung_row(card.get("hostname") or key, card, r.get("received_at"), now))
    seen_hosts = {row["host"].lower() for row in lungs}
    for name in configured_lungs or []:
        if name.lower() not in seen_hosts:
            lungs.append(lung_row(name, None, None, now))
    lungs.sort(key=lambda row: row["host"].lower())
    counts = {"alive": 0, "warn": 0, "dark": 0}
    for row in lungs:
        counts[row["status"]] += 1
    return {"generated_at": iso_utc(now), "beat_minutes": BEAT_MINUTES,
            "dark_after_minutes": DARK_AFTER_MIN, "alive_grace_minutes": ALIVE_GRACE_MIN,
            "proof_stale_minutes": PROOF_STALE_MIN, "ledger": str(ledger),
            "summary": {"lungs": len(lungs), **counts}, "lungs": lungs}


def load_fleet_config(path: str) -> list[dict]:
    """Fleet config: {"lungs": [{name, ssh_alias, remote_root}...]} or a flat
    mapping lung -> ssh alias string / {ssh_alias, remote_root} object."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    lungs: list[dict] = []
    items = cfg.get("lungs") if isinstance(cfg, dict) and isinstance(cfg.get("lungs"), list) else None
    if items is None and isinstance(cfg, dict):
        items = list(cfg.items())
    for item in items or []:
        if isinstance(item, dict) and item.get("name") and item.get("ssh_alias"):
            lungs.append({"name": str(item["name"]), "ssh_alias": str(item["ssh_alias"]),
                          "remote_root": str(item.get("remote_root") or DEFAULT_REMOTE_ROOT)})
        elif isinstance(item, tuple) and isinstance(item[1], (str, dict)):
            name, val = item
            alias = val if isinstance(val, str) else val.get("ssh_alias")
            root = None if isinstance(val, str) else val.get("remote_root")
            if alias:
                lungs.append({"name": str(name), "ssh_alias": str(alias),
                              "remote_root": str(root or DEFAULT_REMOTE_ROOT)})
    return lungs


def pull_card(lung: dict) -> tuple[dict | None, str]:
    """One live pull over ssh. The remote runs the stdlib introducer; stdout
    is the card, everything else is diagnostics."""
    remote = f"cd {lung['remote_root']} && python3 organs/consent-gated-join/mag_introduce.py --json"
    try:
        proc = subprocess.run(["ssh", lung["ssh_alias"], remote],
                              capture_output=True, text=True, timeout=SSH_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, f"{lung['name']}: ssh pull failed ({type(e).__name__}: {str(e)[:120]})"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return None, f"{lung['name']}: ssh exit {proc.returncode} ({tail[-1][:120] if tail else 'no stderr'})"
    try:
        card = json.loads(proc.stdout)
    except ValueError:
        return None, f"{lung['name']}: ssh stdout was not JSON"
    ok, reason = validate_card(card)
    return (card, f"{lung['name']}: card received") if ok else (None, f"{lung['name']}: pulled card rejected ({reason})")


def main() -> int:
    ap = argparse.ArgumentParser(description="collect Mag lung heartbeats into the coordinator ledger + fleet view")
    ap.add_argument("--cards", nargs="*", default=[], help="card JSON files, directories, or glob patterns")
    ap.add_argument("--fleet-config", default=None, help="JSON mapping lung name -> ssh alias for live pulls")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="heartbeat jsonl ledger")
    ap.add_argument("--view", default=str(DEFAULT_VIEW), help="fleet view json output")
    ap.add_argument("--view-only", action="store_true", help="rebuild the fleet view from the ledger, no collection")
    ap.add_argument("--no-prune", action="store_true", help="skip the 7-day retention prune")
    ap.add_argument("--now", default=None, help="ISO timestamp override for the view clock (tests / replay)")
    args = ap.parse_args()

    now = None
    if args.now:
        now = parse_iso(args.now)
        if now is None:
            print(f"ERROR: --now unparseable: {args.now!r}")
            return 2

    ledger, view_path = Path(args.ledger), Path(args.view)
    lungs_cfg: list[dict] = []
    if args.fleet_config:
        try:
            lungs_cfg = load_fleet_config(args.fleet_config)
        except (OSError, ValueError) as e:
            print(f"ERROR: fleet config unreadable: {type(e).__name__}: {e}")
            return 2

    cards: list[dict] = []
    rejects: list[str] = []
    if not args.view_only:
        if args.cards:
            cards, rejects = load_cards_from_args(args.cards)
        for lung in lungs_cfg:
            card, note = pull_card(lung)
            print(note)
            (cards.append(card) if card is not None else rejects.append(note))
        if not cards and not rejects:
            print("nothing to collect: pass --cards and/or --fleet-config (or --view-only)")
            return 2

    appended, skipped = append_cards(cards, ledger, now=now) if cards else (0, 0)
    removed = 0 if args.no_prune else prune_ledger(ledger, now=now)
    view = build_fleet_view(ledger, now=now, configured_lungs=[l["name"] for l in lungs_cfg])
    view_path.parent.mkdir(parents=True, exist_ok=True)
    view_path.write_text(json.dumps(view, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for r in rejects:
        print(f"REJECT {r}")
    print(f"ledger {ledger}: {appended} appended, {skipped} already banked, {removed} pruned")
    s = view["summary"]
    print(f"fleet view {view_path}: {s['lungs']} lungs ({s['alive']} alive, {s['warn']} warn, {s['dark']} dark)")
    for row in view["lungs"]:
        proof = row.get("proof") or {}
        proof_txt = f", proof {proof.get('seconds')}s" if proof.get("ok") else ", no proof"
        print(f"  {row['host']}: {row['status']}, last_seen {row['last_seen']} "
              f"({row['last_seen_age_min']} min), {row['model_count']} models{proof_txt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
