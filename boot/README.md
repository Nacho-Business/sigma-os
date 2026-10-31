# boot - the Kintsugi absorption pipeline

**Invariant:** absorption is consent all the way down. The stick boots to a
four-entry GRUB menu whose default is the harmless live entry; the install
seed's one interactive screen is the disk-level ask; nothing auto-joins the
tailnet (the operator types `sudo tailscale up` - the typed join is the
consent act); the wipe path needs a human to type a disk serial verbatim and
the word WIPE; and a stick built with `--pin-serial` aborts on any foreign
machine before erasing anything (no match, no wipe).

**Falsifier:** any code path that erases, installs, or joins without an
operator act: the render selftest asserts the wipe seed carries no runcmd and
no bootcmd, that the sigma seed's interactive-sections are exactly
[storage], and that no tailscale authkey material rides any seed. The qemu
smoke (kintsugi_smoke.sh, no disk attached, NIC off) proves the menu boots
and captures the screens a human reviews.

**Consumer:** the operator build run on a Linux host with xorriso (WSL works):
`kintsugi_build.sh --selftest`, then build, then `kintsugi_smoke.sh`, then
flash. `sigma_spore_capsule.py` is the drawer-hardware sibling - a single
stdlib file that turns any phone or dead laptop into a proof-of-life spore
door (RFC 8032 vectors in --selftest).
**Verifier:** `kintsugi_selftest.py` (seeds, placeholders, embedded scripts,
grub menu) + `kintsugi_smoke.sh` + the capsule selftest.

Files: kintsugi_build.sh (pipeline), kintsugi_render.py (pure render stage),
kintsugi_smoke.sh (qemu boot), mag_relight_gate.sh (the idempotent relight
core the absorbed box runs after the curated transfer), seeds/ (autoinstall
templates), sigma_spore_capsule.py. Receipts:
watch/inbox/SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md (build, ISO sha256
95da66ee), watch/inbox/SIGMA_REDTEAM_19_ATTACKS_20261030.md (the D-K join
resolution: no authkey on sticks), HANDOFF_SIGMA_OS_PITCH5_SUMMIT_TO_ZAI_
20261030.md (the capsule contract this file re-lands).
