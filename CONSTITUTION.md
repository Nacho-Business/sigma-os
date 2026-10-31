# THE SIGMA CONSTITUTION

The seven articles of SIGMA-OS, the fleet-absorption operating system. Born
2026-10-30 from this fleet's own field failures; each article names the
receipt it was learned from and the enforcement that exists in this tree.
Amendment: operator word + receipt - the same way every law here was born.

Scope: this constitution governs SIGMA-OS (the organs, the Kintsugi boot
pipeline, the fleet configuration, all future lungs). These articles were not
proposed - they were obeyed under fire before they were written down.

## Article 1 - THE CONSENT LAW

A machine joins the swarm by booting the stick its owner was handed, or by a
command its owner runs. Never by autorun. Never by scan. Never by "it was
just on the network."

*Born from:* Mimosa's four blocks of the self-installer (2026-10-30) and the
PoeLLM worm (3,400+ servers via exposed inference endpoints).
*Enforced here:* the Kintsugi boot menu with the live entry as default
(boot/), the install seed's single interactive storage screen, no authkey on
sticks - the typed `sudo tailscale up` is the consent act (boot/seeds),
proof-gated enrollment (organs/consent-gated-join).

## Article 2 - THE PROOF LAW

No proof of generation, no membership. A lung earns its place in the registry
by demonstrating inference live at join time; documents and self-reports are
not proof.

*Born from:* fleet_enroll.py's refusal path and the lane L fact-check
discipline (claims carry receipts or they are not claims).
*Enforced here:* the proof gate plus live re-probe in
organs/consent-gated-join/fleet_enroll.py; spores prove liveness by signing
fresh challenges (boot/sigma_spore_capsule.py). *Owed:* proof freshness
(re-probe cadence) and revocation - the enroll gate admits, nothing yet
expels.

## Article 3 - THE RETENTION LAW

Every instrument carries its own retention bound. A gauge, log, or cache that
grows forever is itself an uninstrumented process.

*Born from:* the 429G accretion (.mimosa 271G + logs 158G, unwatched for
months - THE_429G_DISCOVERY_20261030.md).
*Enforced here:* organs/retention-bounded - logs compress at 14d and die at
30d, the claim log rotates at a size bound, each sweep's own receipt is
bounded at 2000 rows. *Owed:* the operator purge radius.

## Article 4 - THE SIZE LAW

Measure before streaming. No transfer begins without the source size, the
destination free space, and a ceiling that fits both.

*Born from:* the 542G disk-fill (blind tar took a 117G laptop to 100 percent
in an hour).
*Enforced here:* the relight gate guards the disk floor (over 80 percent used
warns, under 10G free stops; boot/mag_relight_gate.sh) and the bootstrap
fails under 60G free (seeds).

## Article 5 - THE JUNK-TIER HONESTY LAW

SIGMA claims latency-tolerant janitor work on reclaimed hardware. It does not
claim frontier capability, cloud replacement, or latency-critical work. When
a claim exceeds the tier, the claim is wrong.

*Born from:* lanes C and O (the economics: $0.075/1M marginal on the
already-on laptop, 104-second generations, and the explicit degrade clauses).
*Enforced here:* the honesty-limits block travels inside the cost report
itself (organs/cost-priced), the falsified routing claim is published in the
fleet matrix (fleet/providers.yaml), and unreceipted numbers are nulls, not
guesses (fleet/sigma_fleet_envelopes.yaml).

## Article 6 - THE VERIFIER LAW (the Anti-Cow clause)

No organ without a named consumer and a named verifier that exists. A
docstring that promises a test file is a contract; a missing one is a
violation.

*Born from:* the lane A audit catching mag_introduce.py's named-but-absent
verifier; the parent estate's standing clause.
*Enforced here:* every directory in organs/ carries a README naming the
invariant, the falsifier, and the consumer, plus its test file; the tree
runs green under pytest before it ships.

## Article 7 - THE BOUNDARY LAW

Fetch-and-execute chains and persistence writes are operator-authorized seat
operations, never silent file-able behavior. The estate may build the
machinery; the machinery does not self-install the machinery.

*Born from:* Mimosa's boundary itself - four blocks that defined where
tracked autonomy ends.
*Enforced here:* the split between the deterministic relight core
(boot/mag_relight_gate.sh, tracked) and the runtime-install recipe
(documented operator step); the wipe path's two-man rule (typed serial,
typed WIPE); nothing in the seeds auto-runs (render selftest asserts no
runcmd, no bootcmd on the wipe seed).

## What this constitution does not govern

Estate-wide matters (driving ladder, width law, receipts doctrine) remain
under the parent estate's CONSTITUTION.md. Device-specific ops belong to each
lung's own runbook. The mycelial-republic constitution governs the wider
republic.

## Ratification

These seven articles were not proposed - they were obeyed under fire before
they were written down. Ratification is the operator's word; until then this
draft governs by precedent, which is how every law above was earned.
