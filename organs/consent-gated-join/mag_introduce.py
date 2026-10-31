#!/usr/bin/env python
# prior_art: tools/fleet_heartbeat_collect.py, tools/offline_boot.py -- the
#   collector reads heartbeat rows coordinator-side and offline_boot gates an
#   existing install; neither renders a bare box's own capability card from
#   its loopback ollama.
"""mag_introduce.py - a Mag lung introducing itself ("hey, this is what we can do").

Consumer: run as the final stage of a relight (tools/mag_relight_gate.sh endgame)
or on demand on any fleet box; the JSON form (--json) is the payload the
desktop-side fleet enrollment (tools/fleet_enroll.py) consumes.
Verifier: tests/test_mag_introduce.py (structure, ollama parse, card render,
loopback fence).

Stdlib only (urllib, json, subprocess, socket, shutil) so it runs on a bare
target box before any venv exists. Talks ONLY to its own loopback ollama -
the SSRF fence (guard_loopback, the auth_switchboard pattern) rejects any
non-loopback scheme/host/resolved address. Read-only self-description.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def guard_loopback(base: str) -> str:
    """SSRF fence (auth_switchboard pattern): http only, loopback hosts only,
    every resolved address must be loopback. This tool describes its OWN box."""
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "http":
        raise ValueError(f"scheme not allowed: {parsed.scheme!r} (http loopback only)")
    host = parsed.hostname or ""
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host outside loopback allowlist: {host!r}")
    for _family, _stype, _proto, _canon, sockaddr in socket.getaddrinfo(host, None):
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_loopback:
            raise ValueError(f"resolved non-loopback address for {host!r}: {ip}")
    return base.rstrip("/")


def _get(base: str, path: str, timeout: float = 5.0):
    with urllib.request.urlopen(guard_loopback(base) + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def ollama_info(base: str = OLLAMA) -> dict:
    info: dict = {"version": None, "models": []}
    try:
        info["version"] = _get(base, "/api/version")["version"]
    except Exception as e:
        info["version"] = f"unreachable ({type(e).__name__})"
        return info
    try:
        info["models"] = [m.get("name") for m in _get(base, "/api/tags").get("models", [])]
    except Exception:
        pass
    return info


def generation_probe(base: str = OLLAMA, model: str | None = None, timeout: float = 240.0) -> dict:
    """One live generate call - the difference between claiming inference and doing it."""
    base = guard_loopback(base)
    try:
        tags = _get(base, "/api/tags").get("models", [])
    except Exception:
        return {"ok": False, "error": "ollama unreachable"}
    if not tags:
        return {"ok": False, "error": "no models pulled"}
    pick = model or tags[0].get("name")
    body = json.dumps({"model": pick, "prompt": "Reply with exactly: READY", "stream": False}).encode()
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{base}/api/generate", data=body), timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
        return {"ok": True, "model": pick, "seconds": round(time.time() - t0, 1), "reply": (out.get("response") or "").strip()[:80]}
    except Exception as e:
        return {"ok": False, "model": pick, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def mem_total_gb() -> str:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return f"{int(line.split()[1]) / 1048576:.1f}G"
    except Exception:
        pass
    return "unknown"


def tailscale_ip() -> str:
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=8)
        ip = (out.stdout or "").strip().splitlines()
        return ip[0] if ip else "not on tailnet"
    except Exception:
        return "not on tailnet"


def introduce(do_probe: bool = True) -> dict:
    mem = shutil.disk_usage("/")
    card = {
        "kind": "mag_lung_introduction.v1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu_count": os.cpu_count(),
        "mem_total": mem_total_gb(),
        "disk_free_gb": round(mem.free / 1073741824, 1),
        "tailscale_ip": tailscale_ip(),
        "ollama": ollama_info(),
    }
    if do_probe:
        card["generation"] = generation_probe()
    return card


def render(card: dict) -> str:
    lines = [
        "=" * 62,
        f"  Mag lung online: {card['hostname']}",
        f"  body: {card['platform']}, {card['cpu_count']} cores, {card['mem_total']} RAM, {card['disk_free_gb']}G free",
        f"  place: tailscale {card['tailscale_ip']}",
    ]
    o = card.get("ollama", {})
    lines.append(f"  voice: ollama {o.get('version')}")
    models = o.get("models") or []
    lines.append(f"  lungs: {', '.join(models) if models else 'NONE - pull models before joining'}")
    g = card.get("generation") or {}
    if g.get("ok"):
        lines.append(f"  proof: {g['model']} generated {g.get('reply')!r} in {g['seconds']}s on this box")
    else:
        lines.append(f"  proof: generation not demonstrated ({g.get('error', 'skipped')})")
    lines.append("  " + "=" * 60)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Mag lung self-introduction (loopback-only, read-only)")
    ap.add_argument("--json", action="store_true", help="machine-readable form (fleet enrollment payload)")
    ap.add_argument("--no-probe", action="store_true", help="skip the live generation call")
    args = ap.parse_args()
    card = introduce(do_probe=not args.no_probe)
    if args.json:
        print(json.dumps(card, indent=2))
    else:
        print(render(card))
    return 0


if __name__ == "__main__":
    sys.exit(main())
