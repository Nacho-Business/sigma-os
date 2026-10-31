#!/usr/bin/env bash
# prior_art: tools/offline_boot.py, scripts/ollama_keepalive.sh -- offline_boot
#   gates an already-installed venv and the keepalive holds a warmed model;
#   neither bootstraps venv, deps and the record surface from a bare Ubuntu
#   24.04 box.
# mag_relight_gate.sh - deterministic relight core for a fleet Linux box.
#
# Scope: venv via get-pip bootstrap, dependency install, record surface, and
# the offline-boot gate. Preconditions (documented recipe, proven on aspire
# 2026-10-30, see watch/inbox/BOB_RELIGHT_NIGHT_20261030.md):
#   - Ubuntu 24.04, estate repo transferred (curated chunked pipeline)
#   - packages: git, curl, zstd (apt)
#   - ollama serving on 127.0.0.1:11434 (runtime install is a logged seat op)
#   - ssh keys ingested, lid hardening applied, tailscale up
# The network-install and persistence stages deliberately live outside tracked
# files; this script is the reproducible post-install core.
#
# Consumer: fleet Linux relights (aspire precedent; desktop migration Phase 4).
# Verifier: organs/consent-gated-join/mag_introduce.py (this tree's own
# instrument: the live generation proof and the card the coordinator enrolls
# from), printed at the end; idempotent re-runs. The estate gate's
# offline_boot.py board is the estate-repo verifier and stays there.
set -u
cd "$(dirname "$0")/.." || { echo "FATAL: run from inside the repo"; exit 2; }

log() { echo "[relight $(date -u +%FT%TZ)] $*"; }
fail() { log "FATAL: $*"; exit 1; }

# ---- preflight -----------------------------------------------------------------------
# Ollama provenance (red-team A9, watch/inbox/SIGMA_REDTEAM_19_ATTACKS_20261030.md;
# SIGMA wave-2 lane 11, 2026-10-30): the kintsugi seed firstboot and the relight
# recipe download the ollama binary from the versionless-latest release URL
# (https://github.com/ollama/ollama/releases/latest/download/...). "latest"
# moves and the fetch carries no checksum, so a MITM with a LAN DHCP/DNS
# position, or an upstream compromise, serves a hostile binary that the
# keepalive then resurrects every 5 minutes. This gate never downloads ollama
# (install is a logged seat op) but it depends on whatever is serving 11434.
# Pin procedure for the seed/recipe owner: replace the latest URL with a
# pinned release-tag URL, take the sha256 published with that GitHub release,
# and verify the tarball against it before untarring. Until that pin lands,
# the first contact with a fresh lung is the recipe's weakest supply-chain
# joint and should ride only a trusted LAN path.
[ -f README.md ] || fail "README.md missing - run from the sigma-os repo root after the curated transfer"
[ -f fleet/providers.yaml ] || fail "fleet/providers.yaml missing - this is not the sigma-os tree"
[ -f LAWS.md ] || fail "LAWS.md missing - the pitfalls block below points at it"
wget -qO- http://127.0.0.1:11434/api/version >/dev/null 2>&1 \
    || fail "ollama not reachable on 11434 - complete the runtime install recipe first"
log "preflight ok: repo present, ollama serving"

# ---- ollama battery (client pulls; bob-calibrated default) ---------------------------
MODELS="${1:-gemma:2b nomic-embed-text deepseek-r1:1.5b}"
export PATH="$HOME/.local/bin:$PATH"
for m in $MODELS; do
    ollama list 2>/dev/null | grep -q "${m%%:*}" || ollama pull "$m" || fail "model pull failed: $m"
done
log "models present: $(ollama list 2>/dev/null | tail -n +2 | wc -l)"

# ---- venv via get-pip (works without python3.12-venv/ensurepip) ----------------------
# get-pip sha256 pin (red-team A9/A14, SIGMA wave-2 lane 11, 2026-10-30):
# when GET_PIP_SHA256 is set the fetched bootstrap is verified against it and
# a mismatch kills the relight. Pin procedure: on a trusted network path run
#   wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
#   sha256sum /tmp/get-pip.py
# and export GET_PIP_SHA256=<sum> for every later relight. Note checked live
# 2026-10-30: https://bootstrap.pypa.io/get-pip.py.sha256 returns 404, so
# there is no published-sum endpoint the gate could fetch on its own; the
# operator pins once from a trusted path and this gate enforces the pin from
# then on. While unset the fetch is UNPINNED and the gate says so loudly.
# Create the venv skeleton if absent (cold-start break G1, walkthrough audit
# 2026-10-30: nothing else in the Kintsugi chain creates it; --without-pip
# needs only the stdlib venv module, then get-pip bootstraps pip below).
if [ ! -x .venv/bin/python ]; then
    python3 -m venv --without-pip .venv || fail "venv skeleton failed"
    log "venv skeleton created (--without-pip; pip bootstraps next)"
fi
if [ ! -x .venv/bin/pip ]; then
    GET_PIP_SHA256="${GET_PIP_SHA256:-}"
    if [ -z "$GET_PIP_SHA256" ]; then
        log "WARN: GET_PIP_SHA256 unset - get-pip.py fetch is UNPINNED (red-team A9/A14 supply-chain risk)"
    fi
    wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py || fail "get-pip.py fetch failed"
    if [ -n "$GET_PIP_SHA256" ]; then
        echo "$GET_PIP_SHA256  /tmp/get-pip.py" | sha256sum -c - >/dev/null 2>&1 \
            || fail "get-pip.py sha256 MISMATCH - pinned $GET_PIP_SHA256 does not match the fetched bytes; refusing pip bootstrap"
        log "get-pip.py sha256 verified: $GET_PIP_SHA256"
    fi
    ./.venv/bin/python /tmp/get-pip.py -q || fail "pip bootstrap failed"
fi
./.venv/bin/pip install -q --upgrade pip
# The release's one dependency: pyyaml (enrollment + seed render verification).
./.venv/bin/pip install -q pyyaml && log "deps installed (pyyaml)" || fail "pyyaml install failed"

# ---- record surface --------------------------------------------------------------------
[ -d .git ] || git init -q -b main
git add -A 2>/dev/null || true
git -c user.email="${USER}@$(hostname)" -c user.name="${USER}-seat" commit -q -m "relight: record surface initialized" 2>/dev/null || true
log "record surface: $(git rev-parse --short HEAD 2>/dev/null || echo 'no commits yet')"

# ---- the gate ---------------------------------------------------------------------------
# This tree's own instrument: the introduction. It probes loopback ollama and
# generates live - the same proof the coordinator's enrollment gate demands.
log "running the introduction - the instrument judges the install"
./.venv/bin/python organs/consent-gated-join/mag_introduce.py
GATE=$?
./.venv/bin/python organs/consent-gated-join/mag_introduce.py --json > "$HOME/introduction.json" 2>/dev/null || true
log "gate exit $GATE (0 = launchable; the card is at ~/introduction.json for the coordinator)"

# ---- known pitfalls (SIGMA wave 2, lane 24, 2026-10-30) --------------------------------
# Consumer: the seat operating the fresh lung, at the moment the gate finishes.
# Verifier: tests/test_mag_relight_gate.py (pitfalls block markers pinned contract-style);
# full doc: docs/KNOWN_PITFALLS.md (travels in the curated docs chunk).
log "Known pitfalls: see LAWS.md in the repo - the five that bite a new lung first:"
log "  1. Disk floor: df -h . before any transfer - over 80% used warns, under 10G free is a hard stop (the 542G night)."
log "  2. Bind policy: ollama binds the tailnet IP only, never 0.0.0.0 - ss -ltn | grep 11434 must not show 0.0.0.0, and the keepalive reverts hand edits (fix the repo copy)."
log "  3. Secrets absence: lung transfers strip .env and memory keys by design - a lung starts with no keys; that is the posture working."
log "  4. Keepalive heal path: if ollama will not restart, check ~/ollama_keepalive.log DOWN rows and check the estate repo's scripts/ollama_keepalive.sh receipt - the pre-fix heal exec'd binary and arg quoted as one filename."
log "  5. Duty-cycle honesty: time one mag cycle before scheduling anything - minutes of CPU on a small box, and gate red rows are hardware law, not failure."

log "RELIGHT CORE COMPLETE - report 'tailscale ip -4' to the desktop seat to join the fleet."
exit "$GATE"
