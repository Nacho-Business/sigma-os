# fleet-visible

**Invariant:** every enrolled lung is always visible in one face: alive,
warn, or DARK. A dead or dropped lung renders DARK with its last seen stamp -
never invisible - and enrolled-but-never-seen lungs render DARK from the
providers.yaml rows alone.

**Falsifier:** a heartbeat ledger row that changes no rendered row, a lung
rendering twice (one box must render once: the enrolled key ollama-bob-2
matches the hostname Bob-2 through the enrollment flattening), or a door that
dies on an absent ledger (absent = empty fleet, not a crash).

**Consumer:** `fleet_heartbeat_collect.py` (coordinator-side collector; the
ledger prunes itself at 7 days - the retention law), `fleet_door.py` (stdlib
HTTP door serving /sigma_fleet.html and /api/v1/sigma/fleet; distilled from
the estate dashboard's SIGMA section), `sigma_fleet.html` (the cockpit page).
**Verifier:** `test_fleet_heartbeat_collect.py` + `test_sigma_fleet_door.py`
(pytest; the door test serves the real routes on an ephemeral port).

Estate receipts: watch/inbox/SIGMA_MAP_06_COCKPIT_20261030.md,
watch/inbox/SIGMA_MAP_08_HEALTH_20261030.md (the G6 dark rule).
