# thermal-decay-guard

**Invariant:** a throttling node stops receiving bulk work the moment its
tok/s decays: current-window tok/s under 0.60 x the session baseline flags
THROTTLE_SUSPECTED, and the demotion block it emits is paste-ready for the
node's providers.yaml row.

**Falsifier:** a decaying ledger that reports STABLE, or a stable ledger that
trips the guard; the fixtures pin all four verdicts (STABLE, RECOVERED,
THROTTLE_SUSPECTED, INSUFFICIENT) on canonical and route1-aliased row shapes.

**Consumer:** the fleet routing seat; the actions JSONL (schema
thermal_guard.v1) is the tripwire a cron or gate reads. This organ is the
first instrument for triangulation blind spot B4 (thermal death-spiral
detection was named, never built, before lane 06).
**Verifier:** `test_thermal_guard.py` (pytest; --report is read-only, exit
codes 0/1/2 pinned).

Estate receipt: SIGMA wave 4 lane 06; watch/inbox/SIGMA_TRIANGULATION_
20261030.md B4.
