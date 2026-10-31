#!/usr/bin/env python
"""fleet_door - the SIGMA fleet cockpit door (one face for lungs-as-machines).

Distilled from the estate's dashboard/server.py SIGMA section (wave 2 lane 05,
2026-10-30; the estate door serves many surfaces, this release carries only the
fleet face): the bounded-tail ledger read, the enrolled-lung rule, the G6 dark
rule applied through the collector's own row builder, and a stdlib HTTP door.

Reads the coordinator heartbeat ledger the collector
(fleet_heartbeat_collect.py, same directory) writes, through a bounded tail
window: the ledger is 7-day bounded by its writer, and the door stays bounded
even if that bound fails (the corpse-door law). Absent ledger = empty fleet;
lungs enrolled in fleet/providers.yaml under `providers:` with an ollama-* key
that the ledger never heard from render DARK ("never seen") so a dead or
dropped lung is visible, not invisible.

Consumer: the operator cockpit page /sigma_fleet.html (same directory) at the
door's port; it fetches /api/v1/sigma/fleet.
Verifier: organs/fleet-visible/test_sigma_fleet_door.py (Anti-Cow block,
SIGMA_MAP_06 section 6, named before landing).

Routes:
  /sigma_fleet.html, /sigma_fleet   the cockpit page
  /api/v1/sigma/fleet               the sigma_fleet.v1 payload
  /                                 index (points at the cockpit)

Stdlib only (pyyaml for the enrolled-row read; an absent or unreadable
providers.yaml simply enrolls nothing). Run: python fleet_door.py [--port 8765].
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ORGAN = Path(__file__).resolve().parent
REPO = ORGAN.parents[1]
SIGMA_LEDGER = REPO / "var" / "fleet" / "heartbeats.jsonl"
PROVIDERS_YAML = REPO / "fleet" / "providers.yaml"
PAGE = ORGAN / "sigma_fleet.html"

if str(ORGAN) not in sys.path:
    sys.path.insert(0, str(ORGAN))
import fleet_heartbeat_collect as fbc  # noqa: E402

TAIL_LIMIT = 2000
TAIL_WINDOW = 262144


def _tail_jsonl(path: Path, limit: int = TAIL_LIMIT, window: int = TAIL_WINDOW) -> dict:
    """Last `limit` rows via a bounded backward read - O(window), never the
    whole file. Returns rows + truncated flag (True when the window may have
    cut older rows)."""
    if not path.is_file():
        return {"rows": [], "truncated": False, "bytes_read": 0}
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > window:
            fh.seek(size - window)
            fh.readline()  # drop the partial line at the window head
            data = fh.read()
            truncated = True
        else:
            data = fh.read()
            truncated = False
    rows = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"rows": rows[-limit:], "truncated": truncated, "bytes_read": len(data)}


def _enrolled_ollama_lungs(providers_path: Path = PROVIDERS_YAML) -> list[str]:
    """Enrolled lung names: providers.yaml rows under `providers:` whose key
    matches ollama-* (the fleet_enroll row shape, e.g. ollama-bob-2). The bare
    `ollama` row is this box's own lane, not an enrolled lung; the dash in the
    pattern keeps it out. Absent or unreadable yaml = no enrolled rows: the
    door never dies on config."""
    import yaml

    try:
        data = yaml.safe_load(providers_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    rows = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return []
    return sorted(str(k) for k in rows if isinstance(k, str) and k.startswith("ollama-"))


def _host_key(host: object) -> str:
    """fleet_enroll._row_name flattening applied in reverse: lowercase,
    non-alnum to '-', edges stripped. The enrolled provider key is
    'ollama-' + this form of the card hostname, so matching ledger hosts to
    enrolled rows runs both sides through it."""
    return "".join(c if c.isalnum() else "-" for c in str(host)).strip("-").lower()


def sigma_fleet_payload(
    ledger: Path = SIGMA_LEDGER,
    providers_path: Path = PROVIDERS_YAML,
    now: datetime.datetime | None = None,
) -> dict:
    """Cockpit feed: latest card per lung from a bounded tail of the heartbeat
    ledger, the G6 dark rule applied (alive <= 20 min grace, warn to 45, DARK
    at 3 missed beats), and enrolled-but-never-seen lungs rendered DARK. Row
    building and thresholds are the lane 13 collector's own
    (fleet_heartbeat_collect.lung_row) so the door and the collector cannot
    drift apart. Enrolled rows match ledger hosts through the fleet_enroll
    flattening (ollama-bob-2 is the row FOR the lung whose hostname is Bob-2),
    so one physical box never renders as two rows."""
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    tail = _tail_jsonl(ledger)
    latest: dict[str, tuple[datetime.datetime, str, dict, Any]] = {}
    for r in tail["rows"]:
        if not isinstance(r, dict):
            continue
        card = r.get("card")
        if not isinstance(card, dict):
            continue
        host = str(r.get("host") or card.get("hostname") or "").strip()
        stamp = fbc.parse_iso(card.get("generated") or "")
        if not host or stamp is None:
            continue
        cur = latest.get(host.lower())
        if cur is None or stamp > cur[0]:
            latest[host.lower()] = (stamp, host, card, r.get("received_at"))
    enrolled = _enrolled_ollama_lungs(providers_path)
    lungs = [
        fbc.lung_row(host, card, received_at, now_dt)
        for _stamp, host, card, received_at in latest.values()
    ]
    seen = {_host_key(row["host"]) for row in lungs}
    for name in enrolled:
        bare = name[len("ollama-"):] if name.startswith("ollama-") else name
        if _host_key(bare) not in seen:
            lungs.append(fbc.lung_row(name, None, None, now_dt))
    lungs.sort(key=lambda row: str(row.get("host", "")).lower())
    counts = {"alive": 0, "warn": 0, "dark": 0}
    for row in lungs:
        status = row.get("status", "dark")
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema": "sigma_fleet.v1",
        "generated_at": fbc.iso_utc(now_dt),
        "beat_minutes": fbc.BEAT_MINUTES,
        "dark_after_minutes": fbc.DARK_AFTER_MIN,
        "alive_grace_minutes": fbc.ALIVE_GRACE_MIN,
        "proof_stale_minutes": fbc.PROOF_STALE_MIN,
        "ledger": str(ledger),
        "ledger_truncated": bool(tail["truncated"]),
        "enrolled_lungs": enrolled,
        "summary": {"lungs": len(lungs), **counts},
        "lungs": lungs,
    }


_INDEX = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>SIGMA fleet door</title></head>
<body style="font-family:monospace;background:#0b0f0d;color:#d6e3d8;padding:2em">
<h1>SIGMA fleet door</h1>
<p><a href="/sigma_fleet.html" style="color:#4fb3a8">/sigma_fleet.html</a> - one row per lung</p>
<p><a href="/api/v1/sigma/fleet" style="color:#4fb3a8">/api/v1/sigma/fleet</a> - sigma_fleet.v1 feed</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    # CLI overrides land here (main); the defaults are the module constants.
    ledger = SIGMA_LEDGER
    providers = PROVIDERS_YAML

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/sigma_fleet.html", "/sigma_fleet"):
            try:
                body = PAGE.read_bytes()
            except OSError:
                self._send(500, b"cockpit page missing", "text/plain")
                return
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/api/v1/sigma/fleet":
            payload = sigma_fleet_payload(ledger=self.ledger, providers_path=self.providers)
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
        elif path == "/":
            self._send(200, _INDEX.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="serve the SIGMA fleet cockpit door")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--ledger", default=str(SIGMA_LEDGER))
    ap.add_argument("--providers", default=str(PROVIDERS_YAML))
    args = ap.parse_args(argv)
    Handler.ledger = Path(args.ledger)
    Handler.providers = Path(args.providers)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fleet door: http://127.0.0.1:{args.port}/sigma_fleet.html "
          f"(ledger {args.ledger})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
