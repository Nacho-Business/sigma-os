"""knot_core - the hash, proof, and chain-consistency core of the knot chain.

Extracted verbatim in function from the estate's mag/verkle_knot.py (the 1479
line module stays there; SIGMA lungs need only this core) so this release's
verify/mint semantics are the library's own and cannot drift from it.

Consumer: checkpoint.py (the mint side: gate + append + tip write) and
verkle_verify.py (the read-only verify side). Verifier:
organs/merkle-chained-memory/test_merkle_memory.py (synthetic chain round
trip, tamper fail-closed, proof round trip, missing chain cannot-run) plus the
estate's own tests/test_t1w1_ractor_fixes.py and tests/test_verkle_verify.py
which pin the same functions against the live chain.

Row/tip formats (compatible with the estate chain, so a lung holding the
15MB chain copy verifies here unchanged):
  verkle_chain.jsonl  one JSON row per leaf; the fields this core reads are
                      leaf_hash and verkle_root (last row's root must equal the
                      root recomputed over ALL leaf hashes)
  verkle_tip.json     schema verkle_tip.v1: root, n_leaves, last_filename,
                      last_leaf_hash, last_session_id, updated_minute

Known property kept on purpose (MERKLE_COLLISION_PROOF_20261007): duplicate-
last padding means a root alone does not bind the leaf count; the cures are
leaf-level position binding (MAG_POSITION_BOUND_LEAVES) and proofs that bind
n_leaves (VerkleProof.verify with n_leaves, T1W1 ticket 12).

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "verkle_knot.v1"
TIP_SCHEMA = "verkle_tip.v1"

# Set by the entry points (checkpoint.py / verkle_verify.py retarget for a
# --root); defaults resolve to the repo's own biography chain.
BIO = Path(__file__).resolve().parents[2] / "var" / "biography"
CHAIN = BIO / "verkle_chain.jsonl"
TIP = BIO / "verkle_tip.json"


def retarget(root: Path) -> None:
    """Point CHAIN/TIP at another root for this process (one-shot CLI use)."""
    global CHAIN, TIP
    bio = Path(root) / "var" / "biography"
    CHAIN = bio / "verkle_chain.jsonl"
    TIP = bio / "verkle_tip.json"


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _leaf_hash(obj: Any, index: int | None = None) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    if index is not None and os.environ.get("MAG_POSITION_BOUND_LEAVES", "") in {"1", "true", "yes", "on"}:
        return _h(b"leaf:" + str(int(index)).encode("ascii") + b":" + raw)
    return _h(b"leaf:" + raw)


def _merkle_root(leaf_hashes: list[str]) -> str:
    # Length-ambiguity defect (MERKLE_COLLISION_PROOF_20261007): duplicate-last
    # padding below means a root alone does not bind the leaf count. Leaf-level
    # position binding (MAG_POSITION_BOUND_LEAVES, see _leaf_hash) is the cure;
    # callers may additionally bind n_leaves (VerkleProof.verify, T1W1 ticket 12).
    if not leaf_hashes:
        return _h(b"empty")
    layer = leaf_hashes[:]
    while len(layer) > 1:
        nxt = []
        for i in range(0, len(layer), 2):
            left = layer[i]
            right = layer[i + 1] if i + 1 < len(layer) else left
            nxt.append(_h(b"node:" + left.encode() + b":" + right.encode()))
        layer = nxt
    return layer[0]


class VerkleProof:
    """Membership proof over the knot chain: index + sibling path -> root.

    verify() reconstructs the root from (index, leaf_hash, sibling path) and
    matches it against the committed root. The invariant equation, executable.
    """

    def __init__(self, index: int, leaf_hash: str, path: list[str], root: str) -> None:
        self.index = index
        self.leaf_hash = leaf_hash
        self.path = path  # sibling hashes, bottom-up
        self.root = root

    def verify(self, leaf_obj: Any | None = None, leaf_hash: str | None = None,
               n_leaves: int | None = None) -> bool:
        """Recreate the root from the pointer + siblings; match the committed root.

        T1W1 ticket 12 (2026-10-01, RACTOR law L3): under duplicate-last
        padding one root covers several leaf counts ([a,b,c] and [a,b,c,c]
        share a root), so a proof that does not bind the leaf count can be
        replayed against a different-length chain: an index at or beyond the
        real n still reconstructs the padded root. When ``n_leaves`` is
        passed, verify refuses any index outside 0..n_leaves-1 and any path
        whose depth is not exactly ceil(log2(n)) ((n-1).bit_length(), the
        depth _merkle_root's halving loop produces). ``prove()`` passes it;
        callers that cannot know n may omit it and get the legacy root-only
        check. Pinned by the estate's tests/test_t1w1_ractor_fixes.py.
        """
        h = leaf_hash or (_leaf_hash(leaf_obj) if leaf_obj is not None else self.leaf_hash)
        if h != self.leaf_hash:
            return False
        if n_leaves is not None:
            if n_leaves < 1 or not (0 <= self.index < n_leaves):
                return False
            if len(self.path) != (n_leaves - 1).bit_length():
                return False
        idx = self.index
        for sib in self.path:
            if idx % 2 == 0:
                h = _h(b"node:" + h.encode() + b":" + sib.encode())
            else:
                h = _h(b"node:" + sib.encode() + b":" + h.encode())
            idx //= 2
        return h == self.root

    def equation(self) -> str:
        """Human-readable invariant: the reconstruction equation."""
        terms = []
        idx = self.index
        for step, sib in enumerate(self.path, 1):
            terms.append(f"n{step}=H({'L' if idx % 2 == 0 else 'R'}+{sib[:8]})")
            idx //= 2
        return f"root = {self.root[:16]}..  <-  " + " -> ".join(terms)


def load_chain_rows(chain: Path | None = None) -> list[dict[str, Any]]:
    """All rows of the chain jsonl (skips blank and malformed lines)."""
    rows: list[dict[str, Any]] = []
    path = chain or CHAIN
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def prove(index: int | None = None, chain: Path | None = None) -> dict[str, Any]:
    """Build an O(log n) inclusion proof for a leaf in the knot chain.

    Default index = the latest leaf. Returns the proof dict (reversible via
    VerkleProof.verify) so a holder can demonstrate real reconstruction.
    """
    rows = load_chain_rows(chain)
    leaves = [r["leaf_hash"] for r in rows if r.get("leaf_hash")]
    if not leaves:
        return {"ok": False, "error": "no verkle leaves committed yet"}
    idx = index if index is not None else len(leaves) - 1
    if not (0 <= idx < len(leaves)):
        return {"ok": False, "error": f"index {idx} out of range (0..{len(leaves)-1})"}
    root = _merkle_root(leaves)
    layer = leaves[:]
    path: list[str] = []
    i = idx
    while len(layer) > 1:
        if i % 2 == 0:
            sib = layer[i + 1] if i + 1 < len(layer) else layer[i]
        else:
            sib = layer[i - 1]
        path.append(sib)
        nxt: list[str] = []
        for j in range(0, len(layer), 2):
            left = layer[j]
            right = layer[j + 1] if j + 1 < len(layer) else left
            nxt.append(_h(b"node:" + left.encode() + b":" + right.encode()))
        layer = nxt
        i //= 2
    proof = VerkleProof(index=idx, leaf_hash=leaves[idx], path=path, root=root)
    return {
        "ok": True,
        "index": idx,
        "n_leaves": len(leaves),
        "leaf_hash": leaves[idx],
        "path": path,
        "root": root,
        # T1W1 ticket 12: the self-verification binds the leaf count it was
        # proved against, not just the root.
        "verified": proof.verify(n_leaves=len(leaves)),
        "equation": proof.equation(),
    }


def verify_chain_consistent(tip_path: Path | None = None,
                            chain_path: Path | None = None) -> tuple[bool, str]:
    """Verify the committed chain is tamper-evident before extending it.

    Recompute the merkle root over ALL leaves once (O(n), not O(n^2)) and check
    it matches the last committed row's root + the tip. Any tampered leaf ->
    root mismatch -> False. This is the integrity GATE: refuse to extend an
    unverified chain (never build on a lie).

    T1W1 ticket 7 (2026-10-01, RACTOR distillation, law L3): a MISSING or
    corrupt tip on a non-empty chain refuses the gate while binding nothing
    otherwise (a root alone does not bind leaf count under duplicate-last
    padding). The expected tip is recomputed from the tail and compared: root
    and n_leaves must match exactly, last_leaf_hash is checked when present.
    Fail-closed is safe: repair is a deliberate, operator-visible act.
    """
    rows = load_chain_rows(chain_path)
    if not rows:
        return True, "empty chain (genesis ok)"
    hashes = [r.get("leaf_hash") for r in rows if r.get("leaf_hash")]
    if not hashes:
        return True, "no leaf hashes yet"
    expect = _merkle_root(hashes)
    if rows[-1].get("verkle_root") != expect:
        return False, (f"last row root mismatch (committed {str(rows[-1].get('verkle_root'))[:16]} "
                       f"vs recomputed {expect[:16]})")
    tip_file = tip_path or TIP
    try:
        tip = json.loads(tip_file.read_text(encoding="utf-8")) if tip_file.is_file() else None
    except (OSError, json.JSONDecodeError):
        tip = None
    if not isinstance(tip, dict) or not tip:
        return False, (f"tip missing or unreadable on a non-empty chain "
                       f"(expected root {expect[:16]}..., {len(hashes)} leaves)")
    if tip.get("root") != expect:
        return False, f"tip root mismatch (tip {str(tip.get('root'))[:16]} vs recomputed {expect[:16]})"
    if tip.get("n_leaves") != len(hashes):
        return False, f"tip n_leaves mismatch (tip {tip.get('n_leaves')} vs recomputed {len(hashes)})"
    if tip.get("last_leaf_hash") and tip.get("last_leaf_hash") != hashes[-1]:
        return False, (f"tip last_leaf_hash mismatch (tip {str(tip.get('last_leaf_hash'))[:16]} "
                       f"vs tail {str(hashes[-1])[:16]})")
    return True, f"chain consistent ({len(hashes)} leaves)"


def tip_for_tail(rows: list[dict[str, Any]], hashes: list[str], root: str) -> dict[str, Any]:
    """The boot pointer is the chain tail, never the session just amended."""
    hashed = [r for r in rows if r.get("leaf_hash")]
    last = hashed[-1] if hashed else {}
    return {
        "schema": TIP_SCHEMA,
        "root": root,
        "n_leaves": len(hashes),
        "last_filename": last.get("filename"),
        "last_leaf_hash": hashes[-1] if hashes else None,
        "last_session_id": last.get("session_id"),
        "updated_minute": last.get("end_minute") or last.get("start_minute"),
        "note": "Tip is the chain tail. An amended older session does not become the boot.",
    }


def write_tip_atomic(tip: dict[str, Any], tip_path: Path | None = None) -> None:
    """Replace the small tip pointer atomically (tmp + os.replace)."""
    target = tip_path or TIP
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(tip, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
