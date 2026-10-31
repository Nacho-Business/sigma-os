# merkle-chained-memory

**Invariant:** the record is a hash-chained append-only ledger whose tip
recomputes from every leaf: nothing can be edited or removed without breaking
the root, and nothing new is minted on an unverified chain (never build on a
lie).

**Falsifier:** flip one byte of one leaf hash and both faces must refuse -
`verkle_verify` exits 1, and `checkpoint mint` refuses to extend (exit 1,
nothing appended). A proof that does not bind the leaf count is replayable
against a different-length chain, so proofs verify with n_leaves bound
(T1W1 ticket 12).

**Consumer:** `checkpoint.py mint` (the write face) and `verkle_verify.py`
(the read face a lung runs at boot against its 15MB chain copy). Both call
`knot_core.py`, extracted from the estate's mag/verkle_knot.py, so the two
faces cannot drift from the library.
**Verifier:** `test_merkle_memory.py` (synthetic chain: mint-verify round
trip, tamper fail-closed, proof round trip, missing chain cannot-run).

Provenance: the estate's dual-witnessed mint (mag/mesh/checkpoint.py) lives on
the aspire surface only (moonshot-3 finding 3); this organ re-lands the mint
half on the core this tree ships, with witnessing kept an operator-led act.
Chain rows are verkle_knot.v1-compatible: an estate chain copy verifies here
unchanged. Receipts: watch/inbox/SIGMA2_08_VERKLE_VERIFY_20261030.md,
SIGMA_MAP_03_MEMORY_PLANE_20261030.md.
