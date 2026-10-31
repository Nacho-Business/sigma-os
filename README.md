# SIGMA-OS

**Or: how to stop renting your brain and build the forest.** A field-tested
operating system for a sovereign household AI fleet: reclaimed hardware, local
inference, proof-gated membership, receipt-chained memory, and a constitution
the machines obeyed before anyone wrote it down.

Every number in this repo is real and receipted (see RECEIPTS.md). Every
failure named is ours. The hardware is in a garage.

## Why

Sam Altman says the RAM in your own computer is "a terrible allocation of
scarce resources." We agree with the measurement and reject the conclusion.
The idle hardware you already own is enough to run a latency-tolerant AI
fleet for the price of its idle watts - this fleet banked a 90-generation
routing experiment at $0.0051 of marginal electricity, and published the
falsified routing claim against itself when the data disagreed.

The forest does not fight the subscription model. It exists as the proof that
another allocation is possible: sovereign nodes, voluntary connections,
receipt-based memory, proof-gated membership, and a falsifier that kills every
claim before it becomes a lie.

## The seven articles (CONSTITUTION.md)

1. **Consent** - a machine joins only by an act its owner performs.
2. **Proof** - no proof of generation, no membership.
3. **Retention** - every instrument carries its own retention bound.
4. **Size** - measure before streaming.
5. **Junk-tier honesty** - when a claim exceeds the tier, the claim is wrong.
6. **Verifier** (the Anti-Cow clause) - no organ without a named consumer and
   a named verifier that exists.
7. **Boundary** - the machinery does not self-install the machinery.

## Layout

    organs/       seven organs, each enforcing one invariant (README in each)
    boot/         the Kintsugi absorption pipeline + the standalone spore
    fleet/        capability-routed provider matrix, node keys, envelopes
    kernel/       C99 native organs (pure libc/POSIX, self-testing)

## Install

Requirements: Python 3.11+, `pyyaml` (organs/consent-gated-join and the
kintsugi selftest), a C compiler for kernel/ (optional), bash for the
retention sweeps (optional).

    git clone <this repo> && cd sigma-os
    python -m pytest organs -q          # every organ green, no network, no GPU

Absorb a machine (the Kintsugi stick, on a Linux host with xorriso):

    boot/kintsugi_build.sh --selftest
    boot/kintsugi_build.sh --password-hash '$6$...' --ssh-key "$(cat ~/.ssh/id_ed25519.pub)"
    boot/kintsugi_smoke.sh --iso out/sigma-kintsugi.iso    # qemu, no disk attached

Boot the target from the stick: the menu defaults to the harmless live entry;
choosing the absorb entry shows one interactive disk screen - that screen and
your typed `sudo tailscale up` are the whole consent ceremony. Enroll the new
lung from its introduction:

    python organs/consent-gated-join/mag_introduce.py --json > introduction.json   # on the lung
    python organs/consent-gated-join/fleet_enroll.py --introduction-file introduction.json  # on the coordinator

No proof of generation, no membership: enrollment dials the lung's own ollama
and refuses unless every carded model answers live.

Resurrect drawer hardware (any Python 3, zero dependencies):

    python boot/sigma_spore_capsule.py --init && python boot/sigma_spore_capsule.py --selftest
    python boot/sigma_spore_capsule.py --port 8768      # the spore door

See a lung die:

    python organs/fleet-visible/fleet_heartbeat_collect.py --cards cards/*.json
    python organs/fleet-visible/fleet_door.py --port 8765    # open /sigma_fleet.html

## Test

    python -m pytest organs -q              # all organs
    python boot/sigma_spore_capsule.py --selftest   # RFC 8032 vectors + door math
    python boot/kintsugi_selftest.py        # seeds, consent screens, grub menu
    cc -std=c99 -O2 -o /tmp/k kernel/sovereign_rib_kernel.c && /tmp/k --selftest
    bash -n organs/retention-bounded/*.sh

## Honesty

SIGMA claims latency-tolerant janitor work on reclaimed hardware. It does not
claim frontier capability, cloud replacement, or latency-critical work. The
cost model is estimates with the honesty limits printed in its own report;
the real instrument is a $15 smart plug. The founding routing thesis was
falsified by this fleet's own instruments and the falsification is published
in the fleet matrix. That is the culture: build the thing, bank the receipt,
let the falsifier kill your favorite idea.

Falsifier for this repo: any number on RECEIPTS.md contradicted by its
instrument voids the claim; any organ whose promised verifier is missing
voids the tree. Come to the forest. Bring your drawer bricks.
