#!/usr/bin/env bash
# install_retention.sh - install the SIGMA retention timer units into systemd --user
# (SIGMA wave 2 lane 16, 2026-10-30).
#
# Consumer: Linux lungs run this once (or after any unit change) to arm the five
#           retention timers. Nothing on the Windows desktop runs this; the desktop
#           keeps Windows schtasks until the migration.
# Verifier: organs/retention-bounded/test_retention.py (bash -n, placeholder substitution contract,
#           unit-pair inventory).
#
# What it does, idempotently (safe to re-run any number of times):
#   1. copies ops/systemd/mag-*.service and mag-*.timer into ~/.config/systemd/user/,
#      substituting @REPO_ROOT@ with this repo's absolute path
#   2. systemctl --user daemon-reload
#   3. systemctl --user enable --now for all five timers
#   4. prints the timer board
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_SRC="$SCRIPT_DIR/systemd"
TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

TIMERS="mag-log-smith.timer mag-scan-smith.timer"

if [ "$(uname -s)" != "Linux" ]; then
  echo "install_retention: this installer is for Linux lungs only (systemd user units)." >&2
  echo "  The Windows desktop keeps Windows schtasks until the migration." >&2
  exit 2
fi
if ! command -v systemctl >/dev/null 2>&1; then
  echo "install_retention: systemctl not found; is systemd the init system on this box?" >&2
  exit 2
fi
if [ ! -d "$UNIT_SRC" ]; then
  echo "install_retention: unit source dir missing: $UNIT_SRC" >&2
  exit 2
fi

mkdir -p "$TARGET_DIR"

for unit in "$UNIT_SRC"/mag-*.service "$UNIT_SRC"/mag-*.timer; do
  name="$(basename "$unit")"
  sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" "$unit" > "$TARGET_DIR/$name"
  echo "installed: $TARGET_DIR/$name (repo root: $REPO_ROOT)"
done

systemctl --user daemon-reload

for t in $TIMERS; do
  systemctl --user enable --now "$t"
done

echo
echo "retention timers armed:"
systemctl --user list-timers 'mag-*' --no-pager || true
