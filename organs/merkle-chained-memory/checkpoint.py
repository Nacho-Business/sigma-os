#!/usr/bin/env python3
"""checkpoint - the mint side of the merkle-chained memory organ.

Provenance: the estate's dual-witnessed checkpoint module (mag/mesh/checkpoint.py)
was landed on the aspire surface only; it never existed on this desktop checkout
(watch/inbox/ractor_engine/2026-10-30-sigma-os-v0/moonshot-3.md, finding 3). This
file is the v2 re-landing of the mint half on the knot core this repo actually
ships (knot_core.py, extracted from mag/verkle_knot.py). Witnessing/cosigning
stays an operator-led act; this module mints chain rows and refuses to touch an
inconsistent chain.

The invariant: never build on a lie. Every mint first proves the existing chain
(root recomputed over ALL leaves, tip cross-checked); an inconsistent or
tampered chain refuses with exit 1 and appends nothing. The tip is rewritten
atomically from a full file recompute after the append, so the pointer always
names the file's actual tail.

Consumer: lungs and the operator seal windows (boot prove, session close). The
read side is verkle_verify.py (same directory) - mint here, verify there, and
the two cannot drift because both call knot_core.
Verifier: organs/merkle-chained-memory/test_merkle_memory.py (mint-verify round
trip, tamper refusal, tip repair refusal, missing-chain cannot-run, position
binding flag).

Modes:
  mint --label L [--payload-file F | --payload TEXT] [--root R]
        gate, append one leaf, rewrite the tip, print ONE JSON row verdict.
        Exit 0 minted, 1 refused (inconsistent chain or bad input).
  status [--root R]  print the tip + leaf census read-only. Exit 0.

Row shape (verkle_knot.v1 compatible): schema, ts, label, payload_sha256,
leaf_hash, verkle_root. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import knot_core as kc  # noqa: E402

ROW_SCHEMA = "verkle_knot.v1"


class MintRefused(Exception):
    """The gate refused: inconsistent chain, bad input. main turns this into exit 1."""


def build_leaf(label: str, payload: str, ts: str | None = None) -> dict:
    """One checkpoint leaf: the label, a content hash, and the timestamp."""
    if not label or not label.strip():
        raise MintRefused("label is required (a checkpoint without a name is unfalsifiable)")
    if payload is None:
        raise MintRefused("payload is required (--payload TEXT or --payload-file F)")
    return {
        "schema": ROW_SCHEMA,
        "ts": ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": str(label),
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "payload_bytes": len(payload.encode("utf-8")),
    }


def mint(label: str, payload: str, root: Path | None = None, ts: str | None = None) -> dict:
    """Gate, append, recompute, tip. Returns the verdict dict; raises MintRefused."""
    if root is not None:
        kc.retarget(Path(root))
    leaf = build_leaf(label, payload, ts=ts)
    ok, why = kc.verify_chain_consistent()
    if not ok:
        raise MintRefused(f"chain gate refused, nothing appended: {why}")
    rows = kc.load_chain_rows()
    hashes = [r.get("leaf_hash") for r in rows if r.get("leaf_hash")]
    leaf["index"] = len(hashes)
    leaf["leaf_hash"] = kc._leaf_hash(leaf)
    hashes.append(leaf["leaf_hash"])
    leaf["verkle_root"] = kc._merkle_root(hashes)
    kc.CHAIN.parent.mkdir(parents=True, exist_ok=True)
    with kc.CHAIN.open("a", encoding="utf-8") as f:
        f.write(json.dumps(leaf, sort_keys=True) + "\n")
    # Recompute from the file (not from memory) so the tip names the real tail
    # even if another writer appended between our read and our write.
    final_rows = kc.load_chain_rows()
    final_hashes = [r.get("leaf_hash") for r in final_rows if r.get("leaf_hash")]
    final_root = kc._merkle_root(final_hashes)
    if final_rows[-1].get("verkle_root") != final_root:
        raise MintRefused("post-append recompute disagrees with the appended row; inspect the chain")
    tip = kc.tip_for_tail(final_rows, final_hashes, final_root)
    kc.write_tip_atomic(tip)
    return {
        "ok": True,
        "minted_index": leaf["index"],
        "leaf_hash": leaf["leaf_hash"],
        "root": final_root,
        "n_leaves": len(final_hashes),
        "label": leaf["label"],
    }


def status(root: Path | None = None) -> dict:
    """Read-only census: tip fields + leaf count + consistency verdict."""
    if root is not None:
        kc.retarget(Path(root))
    ok, why = kc.verify_chain_consistent()
    tip: dict = {}
    if kc.TIP.is_file():
        try:
            loaded = json.loads(kc.TIP.read_text(encoding="utf-8"))
            tip = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            tip = {}
    rows = kc.load_chain_rows()
    return {
        "ok": ok,
        "why": why,
        "n_leaves": len([r for r in rows if r.get("leaf_hash")]),
        "tip_root": tip.get("root"),
        "tip_n_leaves": tip.get("n_leaves"),
        "chain": str(kc.CHAIN),
        "tip": str(kc.TIP),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="checkpoint",
        description="Mint a fail-closed checkpoint leaf into the verkle knot chain.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_mint = sub.add_parser("mint", help="gate + append one leaf + rewrite the tip")
    p_mint.add_argument("--label", required=True, help="what this checkpoint seals (short name)")
    p_mint.add_argument("--payload", default=None, help="inline payload text (hashed, not stored)")
    p_mint.add_argument("--payload-file", default=None, help="hash this file's bytes as the payload")
    p_mint.add_argument("--root", default=None, help="repo root holding var/biography/verkle_chain.jsonl")
    p_mint.add_argument("--ts", default=None, help="ISO timestamp override (default now)")

    p_status = sub.add_parser("status", help="print tip + leaf census (read-only)")
    p_status.add_argument("--root", default=None, help="repo root holding var/biography/verkle_chain.jsonl")

    args = ap.parse_args(argv)
    root = Path(args.root) if getattr(args, "root", None) else None
    if args.cmd == "status":
        print(json.dumps(status(root)))
        return 0
    payload: str | None = args.payload
    if args.payload_file:
        try:
            payload = Path(args.payload_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"ok": False, "error": f"cannot read payload file: {exc}"}))
            return 1
    try:
        verdict = mint(args.label, payload, root=root, ts=args.ts)
    except MintRefused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
