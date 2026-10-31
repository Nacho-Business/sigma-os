#!/usr/bin/env python3
"""verkle_verify - standalone fail-closed verifier for the knot chain.

Provenance: estate tools/verkle_verify.py (SIGMA wave 2 lane 08, 2026-10-30;
receipt watch/inbox/SIGMA2_08_VERKLE_VERIFY_20261030.md), re-parented onto this
release's knot_core.py so the verify semantics are the library's own and cannot
drift from the mint side (checkpoint.py).

Consumer: SIGMA memory-plane lungs and the operator boot check. A lung holding
only the chain copy (var/biography/verkle_chain.jsonl + verkle_tip.json) runs
this read-only at boot to prove its copy before use.
Verifier: organs/merkle-chained-memory/test_merkle_memory.py (mint-verify
round trip on a synthetic chain, tampered copy exit 1, proof round-trip,
tampered-proof exit 1, missing-chain cannot-run).

Read-only by construction: never writes a file, never calls the mint writer.
The one mutation is retargeting the CHAIN/TIP module globals to the --root
paths inside this one-shot process before calling the pinned core functions
(verify_chain_consistent, prove, VerkleProof.verify). The retarget happens in
a separate CLI process, never in-process under pytest (the estate's
tools/prepush_verkle_hermeticity.py exists because unretargeted verkle globals
once let a pytest run overwrite a live chain).

Modes:
  (default)     verify chain + tip: merkle root recomputed over ALL leaves and
                compared to the last row's verkle_root, then tip root, tip
                n_leaves and tip last_leaf_hash, exactly as
                knot_core.verify_chain_consistent does. Prints ONE JSON verdict
                line (ok, n_leaves, tip_root, why) and exits.
  --proof PATH  after a passing chain verdict, verify a saved proof JSON
                (the dict checkpoint/knot_core prove prints) as membership in
                the verified chain: proof.root must equal the verified chain
                root, then VerkleProof.verify(n_leaves=tip n_leaves) binds
                index range and path depth (T1W1 ticket 12).
  --prove INDEX print a saved-proof JSON for leaf INDEX (refuses on an
                inconsistent chain) so --proof has an end-to-end producer.

Exit codes: 0 verified, 1 tampered or inconsistent (fail-closed), 2 cannot
run (chain file absent under --root: nothing to verify is not a pass).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ORGAN = Path(__file__).resolve().parent
if str(ORGAN) not in sys.path:
    sys.path.insert(0, str(ORGAN))

import knot_core as vk  # noqa: E402

ROOT = ORGAN.parents[1]


def _retarget(root: Path) -> None:
    """Point the module's chain + tip globals at --root for this process only."""
    vk.retarget(root)


def _read_tip() -> dict[str, object]:
    """Best-effort tip read for the verdict line (verify already fail-closes)."""
    try:
        val = json.loads(vk.TIP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _chain_verdict() -> tuple[int, dict[str, object]]:
    if not vk.CHAIN.is_file():
        return 2, {
            "ok": False,
            "n_leaves": None,
            "tip_root": None,
            "why": f"cannot run: no chain file at {vk.CHAIN}",
        }
    ok, why = vk.verify_chain_consistent()
    tip = _read_tip()
    verdict: dict[str, object] = {
        "ok": ok,
        "n_leaves": tip.get("n_leaves"),
        "tip_root": tip.get("root"),
        "why": why,
    }
    return (0 if ok else 1), verdict


def _eval_proof(raw: str, chain_root: object, n_leaves: object) -> tuple[bool, dict[str, object]]:
    """Saved-proof membership against the already-verified chain. Fail-closed."""
    extra: dict[str, object] = {"proof_ok": False, "proof_index": None}
    if not isinstance(n_leaves, int) or isinstance(n_leaves, bool) or n_leaves < 1:
        extra["why_proof"] = "no usable tip n_leaves to bind the proof against"
        return False, extra
    try:
        data = json.loads(raw)
        proof = vk.VerkleProof(
            index=int(data["index"]),
            leaf_hash=str(data["leaf_hash"]),
            path=[str(sib) for sib in data["path"]],
            root=str(data["root"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        extra["why_proof"] = f"malformed proof: {exc}"
        return False, extra
    extra["proof_index"] = proof.index
    if proof.root != chain_root:
        extra["why_proof"] = "proof root is not the verified chain root"
        return False, extra
    if proof.verify(n_leaves=int(n_leaves)):
        extra["proof_ok"] = True
        extra["why_proof"] = "proof verified: index + sibling path rebuild the chain root"
        return True, extra
    extra["why_proof"] = "proof reconstruction failed"
    return False, extra


def _cmd_prove(index: int) -> int:
    ok, why = vk.verify_chain_consistent()
    if not ok:
        print(json.dumps({"ok": False, "error": f"chain inconsistent, refusing to prove: {why}"}))
        return 1
    res = vk.prove(index)
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verkle_verify",
        description="Fail-closed read-only verify of the biography verkle chain + tip.",
    )
    parser.add_argument(
        "--root", default=str(ROOT),
        help="repo root holding var/biography/verkle_chain.jsonl + verkle_tip.json",
    )
    parser.add_argument(
        "--proof", default="",
        help="after a passing chain verdict, verify this saved proof JSON (from --prove)",
    )
    parser.add_argument(
        "--prove", dest="prove", type=int, default=None, metavar="INDEX",
        help="print a proof JSON for leaf INDEX instead of verifying",
    )
    args = parser.parse_args(argv)
    _retarget(Path(args.root))
    if args.prove is not None:
        return _cmd_prove(args.prove)
    code, verdict = _chain_verdict()
    if args.proof and code == 0:
        try:
            raw = Path(args.proof).read_text(encoding="utf-8")
        except OSError as exc:
            verdict.update({"proof_ok": False, "why_proof": f"cannot read proof file: {exc}"})
            print(json.dumps(verdict))
            return 1
        good, extra = _eval_proof(raw, verdict["tip_root"], verdict["n_leaves"])
        verdict.update(extra)
        verdict["ok"] = bool(verdict["ok"]) and good
        code = 0 if good else 1
    print(json.dumps(verdict))
    return code


if __name__ == "__main__":
    sys.exit(main())
