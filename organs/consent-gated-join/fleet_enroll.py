#!/usr/bin/env python
# prior_art: tools/fleet_heartbeat_collect.py, tools/lung_receipt_pull.py -- the
#   collector consumes heartbeat rows and the receipt puller moves lung outbox
#   payloads; neither validates proof-of-generation or inserts a providers.yaml
#   row.
"""fleet_enroll.py - add a new Mag lung (organs/consent-gated-join) to the swarm from its introduction.

Consumer: the desktop seat (or any fleet coordinator) receives a lung's
introduction JSON (organs/consent-gated-join/mag_introduce.py --json, carried by the operator or
ssh) and enrolls it: validates PROOF OF GENERATION (no proof, no membership),
inserts a fleet/providers.yaml row, writes the commit hint for the sweeper.
Cost-ledger producer (ractor_engine 2026-10-30 repair step 3): a successful
enrollment transcribes the card's proof generation into the fleet cost ledger
(DEFAULT_LEDGER, one fleet_cost_job.v1 row via organs/cost-priced/fleet_cost_ledger.py).
Rows land only on an actual registry write: refusals and idempotent no-ops
append nothing. Row consumer: organs/cost-priced/fleet_cost_ledger.py project and the
SIGMA G4 gate whose trip clause reads that ledger (SYNTHESIS.md:10).
The keepalive watchdog (scripts/ollama_keepalive.sh) was read and rejected as
the producer site: it probes /api/version only and never generates, so a row
per tick would book non-generation as cost.
Verifier: tests/test_fleet_enroll.py (insertion into a fixture, yaml integrity,
anchor preservation, proof-gate refusal, the SIGMA wave-2 fences below, and
the ledger rows: a proven enrollment lands exactly one row, NO PROOF OF
GENERATION leaves no row, re-enrollment no-op adds no row).

SIGMA wave-2 hardening (watch/inbox/SIGMA_REDTEAM_19_ATTACKS_20261030.md,
attacks A5/A6/A7/A8, lane 03 2026-10-30). A card is a claim, not evidence;
enrollment now checks the world before writing the registry:

1. LIVE RE-PROBE (A5): before any registry write the coordinator dials the
   lung's http://IP:11434/api/tags ITSELF (live_tags) and requires every card
   model to be present in the live tag list. A replayed card pointed at the
   wrong box dies here; a dead lung dies here. The prober is injectable so
   tests run network-free.
2. NAME-SQUAT fence (A6): the derived row name already present with a
   DIFFERENT endpoint IP than the card claims is a squat or a hijacked
   identity - refused loudly instead of silently no-opping. Idempotency stays
   per-name (same name + same IP = no-op); cross-name same-IP (the lung was
   renamed) updates the existing row in place instead of duplicating it.
3. IP fence (A8): tailscale_ip must parse as CGNAT 100.64.0.0/10 or RFC1918
   (the mag_introduce guard_loopback pattern, private-range edition). The IP
   is the only place a fleet endpoint is built from, so it is the SSRF fence.
4. Model charset fence (A7): every model string must match
   ^[A-Za-z0-9._:/-]+$ before any YAML interpolation; a newline or a yaml
   metacharacter in a model name is an injection attempt and is refused.
5. default_model is the card's generation.model VERBATIM (the proof model) -
   no fallback to the first listed model or any hardcoded default, so the
   row routes to exactly the model the live proof generated on.

Enrollment is consent-shaped by design: the lung must have introduced itself
(its own self-description payload), and the coordinator must be handed that
payload. Nothing scans for or reaches into machines that did not speak.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ANCHOR = "  mlx-cluster:"  # insertion anchor in fleet/providers.yaml (pinned by tests)
REPO = Path(__file__).resolve().parents[2]
OLLAMA_PORT = 11434
# Cost-ledger producer import (ractor_engine 2026-10-30 repair step 3);
# fleet_cost_ledger is stdlib-only, the route1_cost.py tools/-sibling pattern.
_COST_DIR = Path(__file__).resolve().parents[1] / "cost-priced"
if str(_COST_DIR) not in sys.path:
    sys.path.insert(0, str(_COST_DIR))
import fleet_cost_ledger as fcl  # noqa: E402

# Pinned watts row for a newly enrolled lung: the card carries no device
# class, so the default is the small-box CPU lung precedent (ollama-bob-2
# maps to laptop in route1_cost.py DEFAULT_LUNG_CLASSES; laptop is 5W idle /
# 25W inferring in fcl.DEVICE_CLASSES). Override with --class when needed.
DEFAULT_ENROLL_CLASS = "laptop"
DEFAULT_LEDGER = REPO / "var" / "fleet" / "cost_jobs.jsonl"
# A7 fence: model names are opaque identifiers (name[:tag], registry paths).
# Anything outside this set cannot survive YAML interpolation unquoted.
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
# A8 fence: the only networks a lung endpoint may live in. CGNAT is the
# tailscale range; RFC1918 covers LAN-only lungs. Everything else (link-local
# metadata addresses, public IPs, hostnames) is refused before it can reach
# base_url.
PRIVATE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
# Row-level key line inside providers.yaml (two-space indent, then a key).
# Used to find block boundaries for the in-place row replacement.
_ROW_HEAD_RE = re.compile(r"^  [A-Za-z0-9._-]+:")


def _row_name(card: dict) -> str:
    host = (card.get("hostname") or "unknown").lower()
    safe = "".join(c if c.isalnum() else "-" for c in host).strip("-")
    return f"ollama-{safe}"


def _display_host(card: dict) -> str:
    """Sanitized hostname for the row's name line (A7 fence): the raw card
    field is never interpolated into yaml, only its flattened form is."""
    host = str(card.get("hostname") or "unknown")
    safe = "".join(c if c.isalnum() else "-" for c in host).strip("-")
    return safe or "unknown"


def _ip_of(card: dict) -> str:
    ip = card.get("tailscale_ip") or ""
    return ip if ip and ip != "not on tailnet" else ""


def _ip_fence_error(ip: str) -> str | None:
    """A8 SSRF fence: CGNAT 100.64.0.0/10 or RFC1918, nothing else."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return f"tailscale_ip {ip!r} does not parse as an IP address - SSRF fence refusal"
    if addr.version != 4 or not any(addr in net for net in PRIVATE_NETS):
        return (
            f"tailscale_ip {ip} outside CGNAT 100.64.0.0/10 and RFC1918 - SSRF fence refusal "
            f"(a fleet endpoint may only point at a private-range lung)"
        )
    return None


def validate_introduction(card: dict) -> tuple[bool, str]:
    if card.get("kind") != "mag_lung_introduction.v1":
        return False, "not a mag_lung_introduction.v1 payload"
    o = card.get("ollama") or {}
    raw_models = o.get("models") or []
    models = [m for m in raw_models if m]
    if not models:
        return False, "lung reports no models - nothing to enroll"
    for m in models:
        if not MODEL_RE.match(str(m)):
            return False, (
                f"model name {m!r} fails charset validation ^[A-Za-z0-9._:/-]+$ - "
                f"yaml injection fence refusal"
            )
    if not _ip_of(card):
        return False, "lung is not on the tailnet - nowhere to route"
    fence = _ip_fence_error(_ip_of(card))
    if fence:
        return False, fence
    gen = card.get("generation") or {}
    if not gen.get("ok"):
        return False, f"NO PROOF OF GENERATION ({gen.get('error', 'absent')}) - no proof, no membership"
    proof = gen.get("model")
    if not proof:
        return False, "generation carries no proof model - nothing to pin default_model to (no fallback default)"
    if not MODEL_RE.match(str(proof)):
        return False, f"proof model {proof!r} fails charset validation - yaml injection fence refusal"
    if str(proof) not in models:
        return False, (
            f"proof model {proof!r} is not in the lung's declared model list - "
            f"proof/claim mismatch, card refused"
        )
    secs = gen.get("seconds")
    if isinstance(secs, bool) or not isinstance(secs, (int, float)):
        return False, "generation.seconds is not a number - unvalidated card fields are never interpolated"
    return True, "proof accepted"


def live_tags(ip: str, timeout: float = 6.0) -> list[str]:
    """A5 live re-probe: the coordinator dials the lung's ollama ITSELF and
    reads its tag list. Only called after the IP fence, so the URL is built
    from a validated private-range address (http, fixed port, no card text
    beyond that one validated octet string)."""
    url = f"http://{ip}:{OLLAMA_PORT}/api/tags"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    tags = data.get("models") or []
    return [t.get("name") for t in tags if isinstance(t, dict) and t.get("name")]


def _re_probe(card: dict, prober) -> None:
    """Refuse unless the live lung actually serves every card model."""
    ip = _ip_of(card)
    try:
        live = prober(ip)
    except Exception as e:
        raise ValueError(
            f"LIVE RE-PROBE failed for {ip}:{OLLAMA_PORT} ({type(e).__name__}) - "
            f"lung unreachable, no enrollment (a dead lung dies here)"
        ) from e
    card_models = [m for m in ((card.get("ollama") or {}).get("models") or []) if m]
    missing = [m for m in card_models if m not in live]
    if missing:
        raise ValueError(
            f"LIVE RE-PROBE mismatch on {ip}: card models absent from the live /api/tags list: "
            f"{missing} - card replay or stale card, refused"
        )


def build_row(card: dict) -> tuple[str, str]:
    """Return (provider_name, yaml_block). Mirrors the ollama-bob row shape."""
    name = _row_name(card)
    ip = _ip_of(card)
    # exact model names (with tags) - bare names 404 at call time (lane 01, 2026-10-30)
    models = [m for m in (card.get("ollama", {}).get("models") or []) if m]
    seen: list[str] = []
    for m in models:
        if m not in seen:
            seen.append(m)
    gen = card.get("generation") or {}
    # fix 5 (SIGMA wave 2): default_model is the proof model VERBATIM. The
    # validation gate guarantees it exists and is in the declared list; the
    # old first-model/gemma:2b fallbacks are gone on purpose.
    proof_model = str(gen["model"])
    if proof_model not in seen:
        seen.insert(0, proof_model)
    model_list = "\n".join(f"      - {m}" for m in seen)
    block = f"""  {name}:
    name: "Ollama {name} ({_display_host(card)} {ip}) - enrolled {time.strftime('%Y-%m-%d')} via fleet_enroll, proof: {gen.get('model')} {gen.get('seconds')}s"
    kind: openai_compat
    # B2-107 locality: reachable where the FLEET code runs; the lung itself uses loopback.
    base_url_env: null
    base_url: http://{ip}:11434/v1
    api_key_env: null
    api_key_default: ollama
    models:
{model_list}
    default_model: {proof_model}
    tier_max: T1
    free_local: true
    timeout_seconds: 240      # small-box CPU generation is slow; don't clip cold loads
    quota:
      period: none
      max_calls: null
      max_tokens: null

"""
    return name, block


def _existing_endpoints(text: str) -> dict[str, str]:
    """Map provider row name -> endpoint host parsed from its base_url."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    provs = parsed.get("providers") if isinstance(parsed, dict) else None
    out: dict[str, str] = {}
    if not isinstance(provs, dict):
        return out
    for row_name, row in provs.items():
        base = row.get("base_url") if isinstance(row, dict) else None
        host = urllib.parse.urlparse(str(base)).hostname if base else None
        out[str(row_name)] = str(host) if host else ""
    return out


def _replace_row(text: str, old_name: str, new_block: str) -> str:
    """Swap the whole row block for old_name with new_block (update-in-place).
    The block runs from its key line to the next row key, a row-level comment,
    a blank separator, or EOF."""
    lines = text.splitlines(keepends=True)
    start = None
    for i, ln in enumerate(lines):
        if ln.rstrip("\n") == f"  {old_name}:":
            start = i
            break
    if start is None:
        raise ValueError(f"row {old_name!r} not found for in-place update")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].rstrip("\n")
        if not stripped.strip() or _ROW_HEAD_RE.match(stripped) or stripped.startswith("  #"):
            end = j
            break
    replacement = new_block.rstrip("\n")
    if not replacement.endswith("\n"):
        replacement += "\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end:])


def record_proof_row(card: dict, lung: str, ledger_path: str | Path,
                     cls_name: str = DEFAULT_ENROLL_CLASS) -> dict:
    """Transcribe the card's proof generation into the fleet cost ledger.

    The proof is a real generation event measured live on the lung
    (tools/mag_introduce.py generation_probe, wall-clock seconds) and already
    shape-validated here. The row carries the measured seconds, no token count
    (the probe reports none, as fcl.heartbeat_job_row documents), watts from
    the class's pinned DEVICE_CLASSES row, and src=enroll-proof for the
    provenance (coordinator-side transcription of a lung-side measurement).
    Raises ValueError on a malformed proof (build_record_row contract).
    """
    gen = card.get("generation") or {}
    seconds = gen.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("generation.seconds is not a number - unvalidated card fields are never ledgered")
    ts = fcl.parse_ts(card.get("generated"))
    row = fcl.build_record_row(lung, str(gen.get("model") or "?"), float(seconds),
                               None, None, cls_name,
                               ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None,
                               "enroll-proof")
    fcl.append_row(row, ledger_path)
    return row


def enroll_into_yaml(card: dict, yaml_path: Path, prober=None,
                     ledger_path: str | Path | None = None,
                     cls_name: str = DEFAULT_ENROLL_CLASS) -> str:
    """Enroll the card's lung into providers.yaml. ledger_path=None (library
    default) writes no ledger row, main() passes DEFAULT_LEDGER; cls_name
    pins the proof row's watts."""
    ok, reason = validate_introduction(card)
    if not ok:
        raise ValueError(f"enrollment refused: {reason}")
    # A5 fence BEFORE anything registry-shaped happens: the world, not the card.
    _re_probe(card, prober or live_tags)
    name, block = build_row(card)
    ip = _ip_of(card)
    text = yaml_path.read_text(encoding="utf-8")
    existing = _existing_endpoints(text)
    if name in existing:
        if existing[name] == ip:
            raise ValueError(f"provider {name} already present - idempotent no-op")
        raise ValueError(
            f"NAME SQUAT refused: provider {name} is already enrolled with endpoint "
            f"{existing[name]} but this card claims {ip} - same name, different lung; "
            f"investigate before touching the registry (A6 fence)"
        )
    same_ip_other_name = [n for n, host in existing.items() if host == ip]
    if same_ip_other_name:
        # the lung was renamed (or its hostname changed): update the existing
        # row in place instead of enrolling a duplicate endpoint
        old_name = same_ip_other_name[0]
        text = _replace_row(text, old_name, block)
        parsed = yaml.safe_load(text)  # integrity check before writing
        if not isinstance(parsed, dict):
            raise ValueError("yaml parse after in-place update did not yield a mapping")
        yaml_path.write_text(text, encoding="utf-8")
        if ledger_path is not None:
            record_proof_row(card, name, ledger_path, cls_name)
        return name
    if ANCHOR not in text:
        raise ValueError(f"insertion anchor {ANCHOR!r} not found in {yaml_path}")
    text = text.replace(ANCHOR, block + ANCHOR, 1)
    parsed = yaml.safe_load(text)  # integrity check before writing
    if not isinstance(parsed, dict):
        raise ValueError("yaml parse after insertion did not yield a mapping")
    yaml_path.write_text(text, encoding="utf-8")
    if ledger_path is not None:
        record_proof_row(card, name, ledger_path, cls_name)
    return name


def main() -> int:
    ap = argparse.ArgumentParser(description="enroll a Mag lung into the fleet from its introduction JSON")
    ap.add_argument("--introduction-file", required=True, help="path to mag_introduce.py --json payload")
    ap.add_argument("--yaml", default=str(REPO / "fleet" / "providers.yaml"))
    ap.add_argument("--hints-dir", default=str(REPO / "var" / "hints"))
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER),
                    help=f"fleet cost ledger for the proof row (default {DEFAULT_LEDGER})")
    ap.add_argument("--class", dest="cls_name", default=DEFAULT_ENROLL_CLASS,
                    choices=sorted(fcl.DEVICE_CLASSES),
                    help="device class pinning the proof row's watts (default laptop)")
    args = ap.parse_args()
    card = json.loads(Path(args.introduction_file).read_text(encoding="utf-8"))
    ok, reason = validate_introduction(card)
    if not ok:
        print(f"ENROLLMENT REFUSED: {reason}")
        return 1
    yaml_path = Path(args.yaml)
    name = enroll_into_yaml(card, yaml_path, ledger_path=args.ledger,
                            cls_name=args.cls_name)
    ip = _ip_of(card)
    hint = {
        "paths": ["configs/providers.yaml"],
        "message": f"FLEET ENROLL: {name} joined the swarm from its introduction ({card.get('hostname')} {ip}, proof {card['generation'].get('model')} {card['generation'].get('seconds')}s, live re-probe passed)",
    }
    hints = Path(args.hints_dir)
    hints.mkdir(parents=True, exist_ok=True)
    hint_path = hints / f"fleet-enroll-{name}.json"
    hint_path.write_text(json.dumps(hint, indent=2) + "\n", encoding="utf-8")
    print(f"ENROLLED: {name}")
    print(f"  endpoint: http://{ip}:11434/v1")
    print(f"  live re-probe: /api/tags matched the card's model list on {ip}")
    print(f"  yaml: {yaml_path} (validated)")
    print(f"  cost ledger: proof row appended to {args.ledger} (class {args.cls_name})")
    print(f"  commit hint: {hint_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
