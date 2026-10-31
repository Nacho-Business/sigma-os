#!/usr/bin/env python3
"""sigma_spore_capsule - the standalone sovereign spore (one file, stdlib only).

Provenance: the aspire surface carries a standalone RFC 8032 Ed25519 spore
capsule deployed to the drawer phones (watch/inbox/HANDOFF_SIGMA_OS_PITCH5_
SUMMIT_TO_ZAI_20261030.md: t90 + pixel live on :8768, 2026-10-30; the capsule
file itself never landed on this desktop checkout, per
watch/inbox/ractor_engine/2026-10-30-sigma-os-v0/moonshot-3.md finding 3).
This file is the v2 re-landing of that contract on the release tree: a single
Python file with zero dependencies that turns any drawer hardware with a
Python 3 interpreter into a proof-of-life spore.

What a spore is: a sovereign node identity that proves liveness by signing a
freshly issued challenge - membership is earned by costly fresh demonstration
(consumer: organs/consent-gated-join/fleet_enroll.py is the lung-side
enrollment analog; the spore is its drawer-hardware sibling).

Door (http.server, default :8768, bind defaults to loopback - the bind law:
never 0.0.0.0; pass --bind a specific tailnet address when joined):
  GET  /health     {"ok": true, "node": ...}
  GET  /identity   {"node": ..., "public_hex": ...}
  GET  /challenge  {"challenge_hex": ...}   32 fresh random bytes
  POST /respond    {"sig_hex": ...} signing the last challenge -> {"verified": bool}

CLI:
  --init           mint this node's keypair into --state (never committed)
  --selftest       RFC 8032 section 7.1 TEST 1 vectors + sign/verify round
                   trip + tamper rejection + cross-check against the
                   `cryptography` package when it is importable
  --port N         door port (default 8768)
  --bind ADDR      door bind address (default 127.0.0.1)

The Ed25519 core is the RFC 8032 reference algorithm written out in full
(iterative scalar mult; no recursion) so the capsule is auditable in one
sitting. Stdlib only: hashlib, json, os, secrets, sys, time, http.server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- ed25519 (RFC 8032)

B = 256
Q = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493


def _h(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()


def _inv(x: int) -> int:
    return pow(x, Q - 2, Q)


_D = (-121665 * _inv(121666)) % Q
_I = pow(2, (Q - 1) // 4, Q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q != 0:
        x = (x * _I) % Q
    if x % 2 != 0:
        x = Q - x
    return x


_BY = (4 * _inv(5)) % Q
_BASE = [_xrecover(_BY), _BY]  # the Ed25519 base point, affine (x, y)


def _edwards_add(p: list, q: list) -> list:
    x1, y1 = p
    x2, y2 = q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _D * x1 * x2 * y1 * y2)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _D * x1 * x2 * y1 * y2)
    return [x3 % Q, y3 % Q]


def _scalarmult(p: list, e: int) -> list:
    """Iterative double-and-add (the RFC's recursive form blows the stack at
    254-bit scalars under tight interpreter limits)."""
    result = [0, 1]  # the neutral element
    addend = p
    while e > 0:
        if e & 1:
            result = _edwards_add(result, addend)
        addend = _edwards_add(addend, addend)
        e >>= 1
    return result


def _decodeint(s: bytes) -> int:
    return int.from_bytes(s, "little")


def _encodepoint(p: list) -> bytes:
    x, y = p
    return ((y % Q) | ((x & 1) << 255)).to_bytes(32, "little")


def _decodepoint(s: bytes) -> list:
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != (s[31] >> 7) & 1:
        x = Q - x
    return [x, y]


def _secret_expand(seed: bytes) -> int:
    # RFC 8032 clamp: clear the low 3 bits, clear bit 255, SET bit 254
    # (byte0 &= 248; byte31 &= 127; byte31 |= 64).
    return (_decodeint(_h(seed)[:32]) & ((1 << 255) - 8)) | (1 << 254)


def publickey(seed: bytes) -> bytes:
    return _encodepoint(_scalarmult(_BASE, _secret_expand(seed)))


def sign(msg: bytes, seed: bytes, pk: bytes) -> bytes:
    a = _secret_expand(seed)
    h = _h(seed)
    r = _decodeint(_h(h[32:] + msg)) % L
    rp = _encodepoint(_scalarmult(_BASE, r))
    s = (r + _decodeint(_h(rp + pk + msg)) * a) % L
    return rp + s.to_bytes(32, "little")


def checkvalid(sig: bytes, msg: bytes, pk: bytes) -> bool:
    if len(sig) != 64 or len(pk) != 32:
        return False
    rp = _decodepoint(sig[:32])
    a_pt = _decodepoint(pk)
    s = _decodeint(sig[32:])
    h = _decodeint(_h(_encodepoint(rp) + pk + msg))
    return _scalarmult(_BASE, s) == _edwards_add(rp, _scalarmult(a_pt, h))


# ---------------------------------------------------------------- capsule

SCHEMA = "sigma_spore.v1"
DEFAULT_PORT = 8768
CHALLENGE_BYTES = 32
_capsule_state = {"node": "spore", "seed": b"", "pk": b"", "challenge": b"", "minted": ""}


def init_state(node: str, state_path: Path) -> dict:
    """Mint (or load) this spore's keypair. The seed never leaves the state
    file, which lives outside any synced or committed surface by default."""
    if state_path.is_file():
        data = json.loads(state_path.read_text(encoding="utf-8"))
        _capsule_state.update({
            "node": data["node"], "seed": bytes.fromhex(data["seed_hex"]),
            "pk": bytes.fromhex(data["public_hex"]), "minted": data.get("created", ""),
        })
        return data
    seed = secrets.token_bytes(32)
    pk = publickey(seed)
    data = {
        "schema": SCHEMA,
        "node": node,
        "seed_hex": seed.hex(),
        "public_hex": pk.hex(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _capsule_state.update({"node": node, "seed": seed, "pk": pk, "minted": data["created"]})
    return data


def load_state(state_path: Path) -> dict:
    if not state_path.is_file():
        raise SystemExit(f"spore state not found: {state_path} (run with --init first)")
    return init_state("", state_path)


class SporeDoor(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({"ok": True, "schema": SCHEMA, "node": _capsule_state["node"],
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        elif self.path == "/identity":
            self._send({"schema": SCHEMA, "node": _capsule_state["node"],
                        "public_hex": _capsule_state["pk"].hex()})
        elif self.path == "/challenge":
            _capsule_state["challenge"] = secrets.token_bytes(CHALLENGE_BYTES)
            self._send({"challenge_hex": _capsule_state["challenge"].hex()})
        else:
            self._send({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path != "/respond":
            self._send({"ok": False, "error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except ValueError:
            self._send({"ok": False, "verified": False, "error": "body is not JSON"}, 400)
            return
        challenge = _capsule_state.get("challenge") or b""
        if not challenge:
            self._send({"ok": True, "verified": False, "error": "no challenge issued"}, 409)
            return
        try:
            sig = bytes.fromhex(str(req.get("sig_hex") or ""))
        except ValueError:
            self._send({"ok": True, "verified": False, "error": "sig_hex not hex"})
            return
        verified = bool(sig) and checkvalid(sig, challenge, _capsule_state["pk"])
        self._send({"ok": True, "verified": verified, "node": _capsule_state["node"]})


# ---------------------------------------------------------------- selftest

TEST1_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc4"
                           "4449c5697b326919703bac031cae7f60")
TEST1_PK = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a"
                         "0ee172f3daa62325af021a68f707511a")
TEST1_SIG = bytes.fromhex("e5564300c360ac729086e2cc806e828a"
                          "84877f1eb8e5d974d873e06522490155"
                          "5fb8821590a33bacc61e39701cf9b46b"
                          "d25bf5f0595bbe24655141438e7a100b")


def selftest() -> int:
    checks: list[str] = []
    # RFC 8032 section 7.1 TEST 1 (empty message)
    pk = publickey(TEST1_SEED)
    assert pk == TEST1_PK, f"TEST 1 public key mismatch: {pk.hex()}"
    checks.append("rfc8032 test1 publickey")
    sig = sign(b"", TEST1_SEED, TEST1_PK)
    assert sig == TEST1_SIG, f"TEST 1 signature mismatch: {sig.hex()}"
    checks.append("rfc8032 test1 sign")
    assert checkvalid(TEST1_SIG, b"", TEST1_PK) is True
    checks.append("rfc8032 test1 verify")
    # round trip on a fresh key
    seed = secrets.token_bytes(32)
    pk = publickey(seed)
    msg = b"spore liveness probe " + secrets.token_bytes(8)
    sig = sign(msg, seed, pk)
    assert checkvalid(sig, msg, pk) is True
    checks.append("sign/verify round trip")
    # tamper rejection: flipped message bit, flipped sig bit, foreign key
    assert checkvalid(sig, msg + b"x", pk) is False
    assert checkvalid(sig[:-1] + bytes([sig[-1] ^ 1]), msg, pk) is False
    other = publickey(secrets.token_bytes(32))
    assert checkvalid(sig, msg, other) is False
    checks.append("tamper rejection (msg, sig, key)")
    # cross-check against the cryptography package when present
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv = Ed25519PrivateKey.from_private_bytes(TEST1_SEED)
        assert priv.public_key().public_bytes_raw() == TEST1_PK
        lib_sig = priv.sign(b"cross")
        assert checkvalid(lib_sig, b"cross", TEST1_PK) is True
        assert bytes(Ed25519PrivateKey.from_private_bytes(seed).sign(b"cross2")) == sign(b"cross2", seed, pk)
        checks.append("cross-check vs cryptography lib")
    except ImportError:
        checks.append("cross-check vs cryptography lib (skipped: not installed)")
    print(json.dumps({"ok": True, "checks": checks}))
    return 0


def serve(node: str, port: int, bind: str, state_path: Path) -> int:
    if not _capsule_state["seed"]:
        load_state(state_path)
    _capsule_state["node"] = node or _capsule_state["node"]
    httpd = ThreadingHTTPServer((bind, port), SporeDoor)
    print(json.dumps({"serving": True, "node": _capsule_state["node"],
                      "bind": bind, "port": port,
                      "public_hex": _capsule_state["pk"].hex()}))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="standalone SIGMA spore capsule (stdlib only)")
    ap.add_argument("--node", default="spore", help="this spore's name (default spore)")
    ap.add_argument("--state", default=str(Path.home() / ".sigma_spore" / "spore.json"),
                    help="keypair state file (private; default ~/.sigma_spore/spore.json)")
    ap.add_argument("--init", action="store_true", help="mint the keypair and exit")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="bind address (loopback default; the bind law forbids 0.0.0.0)")
    ap.add_argument("--selftest", action="store_true", help="run the RFC 8032 vectors and door math")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.init:
        data = init_state(args.node, Path(args.state))
        print(json.dumps({"ok": True, "node": data["node"],
                          "public_hex": data["public_hex"], "state": args.state}))
        return 0
    return serve(args.node, args.port, args.bind, Path(args.state))


if __name__ == "__main__":
    sys.exit(main())
