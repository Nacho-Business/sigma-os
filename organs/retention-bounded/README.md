# retention-bounded

**Invariant:** every instrument names its own retention bound - logs compress
at 14 days and die at 30, scan caches age while findings never do, the claim
log rotates at a size bound, and each sweep script's own receipt is itself
bounded. A gauge that grows forever is itself an uninstrumented process.

**Falsifier:** any surface here whose file count or byte count grows without
bound (the founding incident: 429G of unwatched accretion, THE_429G_DISCOVERY_
20261030.md); also a sweep that mutates without --apply, or archives whose
sha256 or row count disagrees with the source (rotation aborts, live file
untouched).

**Consumer:** `log_smith.sh` + `scan_smith.sh` run under the systemd user
timers in `systemd/` (arm with `install_retention.sh`, Linux lungs);
`room_rotate.py` is the writer-side seam - writers call `maybe_rotate` before
each append. **Verifier:** `test_retention.py` + `test_room_rotate.py`
(pytest; every CLI is dry-run by default).

Estate receipt: watch/inbox/SIGMA_BURN_LANE_T_OPS_20261030.md (lane T b/f);
the estate repo carries the full five-timer board.
