# consent-gated-join

**Invariant:** a machine joins the swarm only by an act its owner performs -
the lung introduces itself and hands over the card, the coordinator validates
PROOF OF GENERATION (no proof, no membership) and re-probes the lung live
before writing one registry row. Nothing scans; nothing autoruns.

**Falsifier:** any enrollment path that writes a registry row without a live
generation proof, or accepts a card whose models the live box does not serve
(card replay), or interpolates an unvalidated hostname/model/IP into yaml -
each refusal path is a named test.

**Consumer:** `mag_introduce.py` runs on the lung (final stage of the relight,
stdlib-only, loopback SSRF fence); `fleet_enroll.py` runs on the coordinator
and writes fleet/providers.yaml above the `  mlx-cluster:` anchor plus one
cost-ledger row per PROVEN enrollment (refusals and no-ops append nothing).
**Verifier:** `test_mag_introduce.py` + `test_fleet_enroll.py` (pytest,
network-free via injectable probers).

Estate receipts: watch/inbox/SIGMA_REDTEAM_19_ATTACKS_20261030.md (attacks
A5-A8 fences), watch/inbox/SIGMA_HANDOFF_EXECUTION_20261030.md (enrollment
proof 75.4s).
