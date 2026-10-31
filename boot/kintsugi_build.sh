#!/usr/bin/env bash
# kintsugi_build.sh - the one-command kintsugi image pipeline (SIGMA path lane 14).
#
# What it does, in order:
#   1. verify the source ISO sha256 against the flash_usb.ps1 pinned hash
#   2. extract the ISO tree with xorriso -osirrox into a workdir
#   3. capture the eltorito repack flags DERIVED from the source ISO
#      (xorriso -report_el_torito as_mkisofs), never hand-maintained
#   4. render the nocloud seeds (sigma generic, optional serial-pinned variant,
#      wipe) and carry the fresh estate tools onto the ISO
#   5. generate boot/grub/grub.cfg: the four-entry consent menu, default is the
#      harmless live entry (stock cfg kept as grub.cfg.orig)
#   6. repack with xorriso using those derived flags, keeping the original
#      volume label and every signed binary untouched
#   7. print the output ISO sha256
#
# Consumer: the operator build run on a Linux host with xorriso (WSL Ubuntu
# works; Windows Git Bash has no xorriso). Verifier: run --selftest first,
# then tools/kintsugi_smoke.sh (qemu boot to the menu, no disk attached),
# then the checklist in watch/inbox/SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md.
#
# Safety rails: refuses to build with seed placeholders unresolved (operator
# provides the password hash + ssh keys at build time, consent law), refuses a
# torito report without --grub2-mbr / -append_partition, aborts when the
# workdir lacks free space, and never writes to any disk device.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RENDER="$REPO/boot/kintsugi_render.py"
SELFTEST_TOOL="$REPO/boot/kintsugi_selftest.py"
PY="${KINTSUGI_PYTHON:-python3}"
ISO_DEFAULT="/mnt/c/Users/foste/Downloads/ubuntu-24.04.5.1-desktop-amd64.iso"
# pinned by .zcode_scratch/flash_usb.ps1, readback-verified twice 2026-10-29
ISO_SHA256="4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7"

ISO="$ISO_DEFAULT"
WORK=""
OUT=""
PASSWORD_HASH="${SIGMA_PASSWORD_HASH:-}"
PIN_SERIAL=""
EXPECTED_SHA="$ISO_SHA256"
SELFTEST=0
SSH_KEYS=()

die() { echo "kintsugi_build: FATAL: $*" >&2; exit 2; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found on PATH"; }

usage() {
    cat <<'USAGE'
usage: kintsugi_build.sh [options]
  --iso PATH            source ISO          (default: /mnt/c/Users/foste/Downloads/ubuntu-24.04.5.1-desktop-amd64.iso)
  --work DIR            build workdir       (default: <iso dir>/kintsugi_build; needs ~2x ISO + 2G free)
  --out PATH            output ISO          (default: <workdir>/sigma-kintsugi.iso)
  --password-hash HASH  sha512crypt hash ($6$...; openssl passwd -6); or env SIGMA_PASSWORD_HASH
  --ssh-key LINE        authorized key line (repeatable; default: every ~/.ssh/*.pub)
  --pin-serial SERIAL   write the serial-pinned variant and point menu entry 1 at it
                        (wrong-machine guard: no match, no wipe)
  --expected-sha256 HEX source ISO hash override (for a different base image)
  --selftest            parse checks only, no xorriso and no ISO needed
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --iso) ISO="$2"; shift 2 ;;
        --work) WORK="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --password-hash) PASSWORD_HASH="$2"; shift 2 ;;
        --ssh-key) SSH_KEYS+=("$2"); shift 2 ;;
        --pin-serial) PIN_SERIAL="$2"; shift 2 ;;
        --expected-sha256) EXPECTED_SHA="$2"; shift 2 ;;
        --selftest) SELFTEST=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; die "unknown option: $1" ;;
    esac
done

# ---- selftest leg (the parse check; runs anywhere bash + python3 + pyyaml live)
if [ "$SELFTEST" = "1" ]; then
    need "$PY"
    bash -n "${BASH_SOURCE[0]}" || die "bash -n failed on kintsugi_build.sh"
    bash -n "$REPO/tools/kintsugi_smoke.sh" || die "bash -n failed on kintsugi_smoke.sh"
    exec "$PY" "$SELFTEST_TOOL"
fi

echo "=== kintsugi build $(date -u +%FT%TZ) ==="

# ---- preflight
need xorriso
need "$PY"
"$PY" "$RENDER" --help >/dev/null 2>&1 || die "cannot run $RENDER"
[ -f "$ISO" ] || die "source ISO not found: $ISO (pass --iso)"
[ -n "${PASSWORD_HASH:-}" ] || die 'password hash required: --password-hash "$(openssl passwd -6)" or env SIGMA_PASSWORD_HASH'
case "$PASSWORD_HASH" in
    '$6$'*) : ;;
    *) die "password hash must be a sha512crypt hash starting with \$6\$ (openssl passwd -6)" ;;
esac
if [ "${#SSH_KEYS[@]}" -eq 0 ]; then
    for f in "$HOME"/.ssh/*.pub; do
        if [ -f "$f" ]; then
            SSH_KEYS+=("$(tr -d '\r\n' < "$f")")
        fi
    done
fi
[ "${#SSH_KEYS[@]}" -gt 0 ] || die "no ssh public keys: pass --ssh-key or have ~/.ssh/*.pub"
for k in "${SSH_KEYS[@]}"; do
    case "$k" in
        ssh-ed25519*|ssh-rsa*|ecdsa-sha2-*|sk-ssh-ed25519@openssh.com*|sk-ecdsa-sha2-nistp256@openssh.com*) : ;;
        *) die "not an ssh public key line: $k" ;;
    esac
done
echo "keys: ${#SSH_KEYS[@]} authorized key(s) collected"

if [ -z "$WORK" ]; then WORK="$(dirname "$ISO")/kintsugi_build"; fi
if [ -e "$WORK/isofiles" ]; then die "workdir already extracted: $WORK/isofiles (remove or pass --work)"; fi
mkdir -p "$WORK"

# disk guard: extraction + repack need roughly 2x the ISO plus slack
ISO_BYTES=$(stat -c %s "$ISO" 2>/dev/null || stat -f %z "$ISO" 2>/dev/null || true)
[ -n "$ISO_BYTES" ] || die "cannot stat the ISO: $ISO"
AVAIL_KB=$(df -Pk "$WORK" | awk 'NR==2 {print $4}')
NEED_KB=$(( (ISO_BYTES * 2 / 1024) + 2 * 1024 * 1024 ))
if [ "$AVAIL_KB" -lt "$NEED_KB" ]; then
    die "disk guard: $WORK has ${AVAIL_KB}K free, need about ${NEED_KB}K (2x ISO + 2G). Free space first."
fi

# ---- 1. source hash (flash discipline: trust bytes, not reports)
ACTUAL_SHA=$("$PY" - "$ISO" "$EXPECTED_SHA" <<'PYEOF'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
print(h.hexdigest())
sys.exit(0 if h.hexdigest() == sys.argv[2].lower() else 1)
PYEOF
) || die "ISO sha256 mismatch: got $ACTUAL_SHA, expected $EXPECTED_SHA"
echo "source iso sha256 ok: $ACTUAL_SHA"

# ---- 2. extract
echo "extracting ISO tree (this takes a few minutes)..."
xorriso -osirrox on -indev "$ISO" -extract / "$WORK/isofiles" >/dev/null
[ -f "$WORK/isofiles/boot/grub/grub.cfg" ] || die "no boot/grub/grub.cfg in the extracted tree; not a desktop ISO?"
echo "extracted: $WORK/isofiles"
# Fix 2026-10-30 (first real run; receipt watch/inbox/SIGMA2_01_KINTSUGI_EXEC_20261030.md):
# xorriso -osirrox preserves the ISO's read-only file modes (0444), and the render
# stage overwrites boot/grub/grub.cfg in place, which died with EACCES
# (PermissionError on the extracted grub.cfg). The selftest missed it because it
# renders to fresh /tmp fixtures. Make the extracted tree user-writable before any
# stage writes into it.
chmod -R u+w "$WORK/isofiles"

# ---- 3. derive the eltorito repack flags from THIS iso (lane D VERIFY-BUILD item)
xorriso -indev "$ISO" -report_el_torito as_mkisofs > "$WORK/torito_report.txt" 2>/dev/null \
    || die "report_el_torito failed; cannot repack safely"
"$PY" "$RENDER" torito --report-file "$WORK/torito_report.txt" --out "$WORK/repack.flags" \
    || die "torito flag derivation failed"
echo "repack flags: $(cat "$WORK/repack.flags")"

# ---- 4. render the seeds (sigma [+ pinned] + wipe) and carry the estate tools
SEED_ARGS=(--password-hash "$PASSWORD_HASH")
for k in "${SSH_KEYS[@]}"; do SEED_ARGS+=(--ssh-key "$k"); done
if [ -n "$PIN_SERIAL" ]; then SEED_ARGS+=(--pin-serial "$PIN_SERIAL"); fi
"$PY" "$RENDER" render --out "$WORK/isofiles" "${SEED_ARGS[@]}"

# ---- 5. the consent menu
cp "$WORK/isofiles/boot/grub/grub.cfg" "$WORK/isofiles/boot/grub/grub.cfg.orig"
GRUB_ARGS=(--stock-cfg "$WORK/isofiles/boot/grub/grub.cfg.orig" --out "$WORK/isofiles/boot/grub/grub.cfg")
if [ -n "$PIN_SERIAL" ]; then GRUB_ARGS+=(--pinned); fi
"$PY" "$RENDER" grub "${GRUB_ARGS[@]}"
echo "grub menu written (default entry 1 = live; stock kept as grub.cfg.orig)"

# ---- 6. repack with the derived flags, original volume label included
[ -n "$OUT" ] || OUT="$WORK/sigma-kintsugi.iso"
FLAGS="$(cat "$WORK/repack.flags")"
echo "repacking..."
# the flags come from xorriso's own report on the official ISO (quoted volume
# label preserved); eval is what keeps those quotes intact
eval "xorriso -as mkisofs $FLAGS -o '$OUT' '$WORK/isofiles'"
[ -f "$OUT" ] || die "repack produced no output"

# ---- 7. the receipt: hash of what actually landed
OUT_SHA=$("$PY" - "$OUT" <<'PYEOF'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
print(h.hexdigest())
PYEOF
)
echo "=== kintsugi build done ==="
echo "output: $OUT ($(stat -c %s "$OUT") bytes)"
echo "output sha256: $OUT_SHA"
cat <<NEXT

next steps (gated on the operator):
  1. tools/kintsugi_smoke.sh "$OUT"          # qemu, no disk, shoot the menu
  2. one boot with Secure Boot ON (qemu --efi or the real stick) before any swap
  3. flash raw + full readback verify, with THIS sha256 as the Expected value
     (update the Expected line in .zcode_scratch/flash_usb.ps1, or dd + sha256)
NEXT
