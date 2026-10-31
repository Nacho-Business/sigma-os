#!/usr/bin/env bash
# log_smith.sh - daily retention sweep of the repo logs/ directory (SIGMA wave 2 lane 16, 2026-10-30).
#
# Consumer: ops/systemd/mag-log-smith.service + mag-log-smith.timer (daily 03:17, Linux lungs),
#           ExecStart invokes `bash scripts/log_smith.sh --apply`. Humans and tests run it
#           WITHOUT --apply for the dry-run listing.
# Verifier: tests/test_retention_units.py (dry-run-vs-apply discipline, 14d compress radius,
#           30d delete radius, receipt row + 2000-row bound).
#
# Retention bound (SIGMA lane T b law, watch/inbox/SIGMA_BURN_LANE_T_OPS_20261030.md):
#   - logs/ files older than 14 days are compressed (gzip in place)
#   - logs/ files older than 30 days are deleted
#   - this script's own receipt logs/log_smith.jsonl is itself bounded at 2000 rows
#   - DRY-RUN by default: lists what would compress / would delete, mutates nothing.
#     Only an explicit --apply mutates the tree.
#
# Env overrides (tests): LOG_SMITH_DIR replaces the target directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOGS_DIR="${LOG_SMITH_DIR:-$ROOT/logs}"

COMPRESS_DAYS=14
DELETE_DAYS=30
RECEIPT_MAX_ROWS=2000
RECEIPT="$LOGS_DIR/log_smith.jsonl"

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    *) echo "usage: log_smith.sh [--apply]" >&2; exit 2 ;;
  esac
done

if [ ! -d "$LOGS_DIR" ]; then
  echo "log_smith: target dir missing: $LOGS_DIR" >&2
  exit 2
fi

now=$(date +%s)
compress_cut=$((now - COMPRESS_DAYS * 86400))
delete_cut=$((now - DELETE_DAYS * 86400))

compress_list=()
delete_list=()

while IFS= read -r -d '' f; do
  m=$(stat -c %Y "$f" 2>/dev/null || echo "$now")
  case "$f" in
    *.gz|*.zip) continue ;;  # already aged once; the delete radius below owns them
  esac
  if [ "$m" -lt "$delete_cut" ]; then
    delete_list+=("$f")
  elif [ "$m" -lt "$compress_cut" ]; then
    compress_list+=("$f")
  fi
done < <(find "$LOGS_DIR" -maxdepth 1 -type f -print0 2>/dev/null | sort -z)

n_compress=${#compress_list[@]}
n_delete=${#delete_list[@]}

for f in "${compress_list[@]}"; do
  age_days=$(( (now - $(stat -c %Y "$f")) / 86400 ))
  if [ "$APPLY" -eq 1 ]; then
    gzip -9 -f "$f"
    echo "compressed: $f.gz (${age_days}d old)"
  else
    echo "would compress: $f (${age_days}d old)"
  fi
done

# THE LEARN-BEFORE-DELETION LAW (operator, 2026-10-30): extraction precedes deletion.
# The digest lands in var/retention_extracts/ (the permanent record); if the
# digest write fails, the delete aborts - fail-closed, so the learning cannot be skipped.
EXTRACT_DIR="$ROOT/var/retention_extracts"

for f in "${delete_list[@]}"; do
  age_days=$(( (now - $(stat -c %Y "$f")) / 86400 ))
  if [ "$APPLY" -eq 1 ]; then
    if [ "${#delete_list[@]}" -gt 0 ] && [ ! -d "$EXTRACT_DIR" ]; then
      mkdir -p "$EXTRACT_DIR" || { echo "log_smith: FATAL - cannot write the extraction digest; deletion ABORTED (learn-before-deletion law)" >&2; exit 1; }
    fi
    bytes=$(stat -c %s "$f" 2>/dev/null || echo 0)
    sample=""
    if [ "$bytes" -le 65536 ]; then sample=$(head -c 256 "$f" 2>/dev/null | tr -d '\000\r\n' | sed 's/\\/\\\\/g;s/"/\\"/g' | head -c 200); fi
    safe_path=$(printf '%s' "$f" | sed 's/\\/\\\\/g;s/"/\\"/g')
    printf '{"kind":"log_smith_digest","ts":"%s","path":"%s","bytes":%s,"age_days":%s,"sample":"%s"}\n' \
      "$(date -u +%FT%TZ)" "$safe_path" "$bytes" "$age_days" "$sample" >> "$EXTRACT_DIR/log_smith_extracts.jsonl"
    rm -f "$f"
    echo "deleted: $f (${age_days}d old; digest banked)"
  else
    echo "would delete: $f (${age_days}d old)"
  fi
done

if [ "$n_compress" -eq 0 ] && [ "$n_delete" -eq 0 ]; then
  echo "log_smith: nothing to do in $LOGS_DIR (clean under both radii)"
fi

if [ "$APPLY" -eq 1 ]; then
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  dir_json="${LOGS_DIR//\\/\\\\}"  # escape backslashes so the receipt row is valid JSON on any OS
  printf '{"kind":"log_smith","ts":"%s","mode":"apply","dir":"%s","compressed":%d,"deleted":%d}\n' \
    "$ts" "$dir_json" "$n_compress" "$n_delete" >> "$RECEIPT"
  # Receipt is itself retention-bound (2000 rows), per lane T b.
  tail -n "$RECEIPT_MAX_ROWS" "$RECEIPT" > "$RECEIPT.tmp" && mv "$RECEIPT.tmp" "$RECEIPT"
fi

exit 0
