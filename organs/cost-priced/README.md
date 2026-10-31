# cost-priced

**Invariant:** every experiment is priced before it is quoted - watts from a
declared two-state device class, dollars from a declared tariff, and the
honesty limits (estimates are estimates) travel in the report itself.

**Falsifier:** a cost number whose math disagrees with the lane tables
(break-even d* for the lane C constants must land on 9.1 percent duty), or a
row booked to a lung with no providers.yaml endpoint match (route1_cost skips
it, never guesses: a wrong lung books watts to the wrong power class).

**Consumer:** `fleet_cost_ledger.py project` (monthly projection from
heartbeat cards + job rows; --json is the cockpit form), `route1_cost.py`
(experiment rollup from route1 arm results; idempotent appends),
`cost_model.py` is the shared pure model. fleet_enroll (consent-gated-join)
transcribes each enrollment proof here as src=enroll-proof.
**Verifier:** `test_fleet_cost_ledger.py` + `test_route1_cost.py` (pytest).

Estate receipts: watch/inbox/SIGMA_BURN_LANE_O_POWER_20261030.md,
watch/inbox/SIGMA_BURN_LANE_C_ECONOMICS_20261030.md; the founding experiment
banked $0.0051 for 90 generations (SIGMA_ROUTE1_VERDICT_20261030.md).
