#!/usr/bin/env bash
# kintsugi_smoke.sh - boot the kintsugi ISO in qemu to the GRUB consent menu
# and capture PNG screendumps for the human to review.
#
# Safety: NO disk is attached (nothing on this machine can be erased even if a
# wrong entry were picked; subiquity finds no target and aborts) and the NIC is
# off. Default is the BIOS/eltorito leg; --efi boots the OVMF secure-boot pair
# when the ovmf package is installed (the SB green check).
#
# Consumer: the operator verification leg, step 1 of the build receipt
# (watch/inbox/SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md).
# Verifier: the shot review checklist in that receipt: the menu must show the
# SIGMA absorb entry, the live entry as default with a countdown, the SIGMA
# wipe entry, and (under EFI) the UEFI Firmware Settings entry.
#
# usage: kintsugi_smoke.sh --iso PATH [--shots DIR] [--efi] [--wait SECS]
#                          [--step SECS] [--smp N] [--mem MB]
set -euo pipefail

PY="${KINTSUGI_PYTHON:-python3}"
ISO=""
SHOTS=""
EFI=0
WAIT=160
STEP=20
SMP=4
MEM=8192

die() { echo "kintsugi_smoke: FATAL: $*" >&2; exit 2; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found on PATH"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --iso) ISO="$2"; shift 2 ;;
        --shots) SHOTS="$2"; shift 2 ;;
        --efi) EFI=1; shift ;;
        --wait) WAIT="$2"; shift 2 ;;
        --step) STEP="$2"; shift 2 ;;
        --smp) SMP="$2"; shift 2 ;;
        --mem) MEM="$2"; shift 2 ;;
        -h|--help) grep -E '^# (usage|Safety|Consumer|Verifier)' "$0" | sed 's/^# //'; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[ -n "$ISO" ] || die "--iso PATH is required"
[ -f "$ISO" ] || die "ISO not found: $ISO"
need qemu-system-x86_64
need "$PY"
if [ -z "$SHOTS" ]; then SHOTS="$PWD/kintsugi_smoke_shots"; fi
mkdir -p "$SHOTS"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/kintsugi-smoke-XXXXXX")"
QMP="$TMP/qmp.sock"
PIDFILE="$TMP/qemu.pid"

cleanup() {
    if [ -f "$PIDFILE" ]; then
        PID="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
        fi
    fi
}
trap cleanup EXIT INT TERM

cat > "$TMP/qmp_shot.py" <<'PYEOF'
import json, socket, sys, zlib, struct


def connect(sock_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(60)
    s.connect(sock_path)
    return s, s.makefile("rwb", buffering=0)


def readobj(f):
    while True:
        line = f.readline()
        if not line:
            raise RuntimeError("qmp connection closed")
        line = line.strip()
        if line:
            return json.loads(line)


def cmd(f, execute, **arguments):
    payload = {"execute": execute}
    if arguments:
        payload["arguments"] = arguments
    f.write((json.dumps(payload) + "\n").encode())
    f.flush()
    while True:
        obj = readobj(f)
        if "return" in obj:
            return obj["return"]
        if "error" in obj:
            raise RuntimeError(f"qmp error: {obj['error']}")


def ppm_to_png(data):
    """qemu screendump writes P6 width height 255 raster; wrap it as PNG."""
    magic, dims, _maxval, raster = data.split(b"\n", 3)
    if magic.strip() != b"P6":
        raise ValueError(f"not a P6 ppm: {magic!r}")
    w, h = (int(x) for x in dims.split())
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += raster[y * stride:(y + 1) * stride]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


mode, sock = sys.argv[1], sys.argv[2]
s, f = connect(sock)
greeting = readobj(f)
assert "QMP" in greeting, f"unexpected qmp greeting: {greeting}"
cmd(f, "qmp_capabilities")
if mode == "shot":
    ppm, png = sys.argv[3], sys.argv[4]
    cmd(f, "screendump", filename=ppm)
    with open(ppm, "rb") as fh:
        data = fh.read()
    with open(png, "wb") as fh:
        fh.write(ppm_to_png(data))
    print(png)
elif mode == "quit":
    try:
        f.write(b'{"execute": "quit"}\n')
        f.flush()
        readobj(f)
    except Exception:
        pass
s.close()
PYEOF

# Fix receipt 2026-10-30 (SIGMA_W4_02): existence+writability of /dev/kvm is
# not usability. The WSL2 kernel 6.18.54.1 exposes the node while kvm_intel
# cannot load ("Operation not supported"), and qemu died at launch with
# "Could not access KVM kernel module: No such device" before any shot was
# taken. Probe the accelerator for real on a none-machine before choosing it.
if [ -c /dev/kvm ] && [ -w /dev/kvm ] \
   && qemu-system-x86_64 -machine none -accel kvm -display none -nodefaults >/dev/null 2>&1; then
    ACCEL="-enable-kvm -cpu host"
else
    ACCEL="-cpu max"
    echo "note: no usable /dev/kvm; firmware emulation is slower, shots may need --wait raised"
fi

PFLASH=()
if [ "$EFI" = "1" ]; then
    FOUND=""
    for pair in \
        "/usr/share/OVMF/OVMF_CODE_4M.ms.fd /usr/share/OVMF/OVMF_VARS_4M.ms.fd" \
        "/usr/share/OVMF/OVMF_CODE.ms.fd /usr/share/OVMF/OVMF_VARS.ms.fd" \
        "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd /usr/share/OVMF/OVMF_VARS_4M.secboot.fd" \
        "/usr/share/ovmf/OVMF_CODE_4M.secboot.fd /usr/share/ovmf/OVMF_VARS_4M.secboot.fd"
    do
        set -- $pair
        if [ -f "$1" ] && [ -f "$2" ]; then
            FOUND="$1 $2"
            break
        fi
    done
    [ -n "$FOUND" ] || die "no OVMF secure-boot code/vars pair found (apt install ovmf); do the SB check on the real stick instead"
    set -- $FOUND
    cp "$2" "$TMP/vars.fd"
    PFLASH=(-drive "if=pflash,format=raw,readonly=on,file=$1" -drive "if=pflash,format=raw,file=$TMP/vars.fd")
    echo "efi leg: $1 (secure boot keys)"
else
    echo "bios leg: SeaBIOS eltorito path"
fi

echo "booting qemu (no disk attached, network off); firmware post takes a moment..."
qemu-system-x86_64 $ACCEL -m "$MEM" -smp "$SMP" \
    "${PFLASH[@]+"${PFLASH[@]}"}" \
    -display none -vga std \
    -cdrom "$ISO" -boot d -no-reboot \
    -net none \
    -qmp unix:"$QMP",server,nowait \
    -D "$TMP/qemu.log" \
    -daemonize -pidfile "$PIDFILE" 2> "$TMP/qemu.launch.err" \
    || die "qemu failed to launch: see $TMP/qemu.launch.err and $TMP/qemu.log"
sleep 1
[ -f "$PIDFILE" ] || die "qemu exited immediately: see $TMP/qemu.launch.err"

t=0
while [ "$t" -lt "$WAIT" ]; do
    sleep "$STEP"
    t=$((t + STEP))
    if "$PY" "$TMP/qmp_shot.py" shot "$QMP" "$TMP/menu_$t.ppm" "$SHOTS/menu_${t}s.png" 2>>"$TMP/qmp_shot.err"; then
        echo "shot: $SHOTS/menu_${t}s.png"
    else
        echo "WARNING: shot at ${t}s failed (qemu may have exited): see $TMP/qmp_shot.err"
        break
    fi
done

"$PY" "$TMP/qmp_shot.py" quit "$QMP" 2>/dev/null || true
sleep 2

cat <<CHECKLIST

smoke shots in: $SHOTS  (qemu scratch: $TMP)

review checklist (receipt: watch/inbox/SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md):
  [ ] a shot shows the GRUB menu, not a kernel panic or a missing-grub error
  [ ] entry 1: SIGMA absorb (interactive screen note, or PINNED note)
  [ ] entry 2: Try or install Ubuntu (live, no changes) with the countdown
  [ ] entry 3: SIGMA wipe (decommission, console two-man rule)
  [ ] EFI boots show entry 4: UEFI Firmware Settings
  [ ] with --efi: Secure Boot left enabled (no red firmware warnings)
nothing was erased: qemu had no disk and no network.
CHECKLIST
