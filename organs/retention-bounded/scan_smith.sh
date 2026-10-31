#!/usr/bin/env bash
# scan_smith.sh - weekly aging sweep of .mimosa scan CACHE subdirs (SIGMA wave 2 lane 16, 2026-10-30).
#
# Consumer: ops/systemd/mag-scan-smith.service + mag-scan-smith.timer (weekly Sun 04:17, Linux lungs),
#           ExecStart invokes `bash scripts/scan_smith.sh --apply`. Humans and tests run it
#           WITHOUT --apply for the dry-run listing.
# Verifier: tests/test_retention_units.py (cache-vs-findings split, dry-run-vs-apply discipline,
#           keep-side never touched, receipt row + 2000-row bound).
#
# Retention bound (SIGMA lane T b + THE_429G_DISCOVERY_20261030 law: scan findings and receipts
# must survive, scan caches may age; the purge radius itself stays an operator call):
#   - cache subdirs (default: history, hook-state, hook-status): files older than 30d are
#     compressed (gzip in place). NOTHING is ever deleted here - aging is compression only.
#   - keep side, never aged: .mimosa/finding-ledger and .mimosa/reports, plus any subdir not
#     in the cache list. Findings and receipts survive forever.
#   - this script's own receipt logs/scan_smith.jsonl is itself bounded at 2000 rows.
#   - DRY-RUN by default: lists what would age, mutates nothing. Only an explicit --apply ages.
#
# Env overrides (tests): MIMOSA_DIR replaces .mimosa, MIMOSA_CACHE_DIRS replaces the cache
# subdir list (space separated), SCAN_SMITH_LOG_DIR replaces the receipt directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MIMOSA_DIR="${MIMOSA_DIR:-$ROOT/.mimosa}"
CACHE_DIRS="${MIMOSA_CACHE_DIRS:-history hook-state hook-status}"
KEEP_DIRS="finding-ledger reports"  # named keep side; kept even if someone widens the cache list
AGE_DAYS=30
RECEIPT_MAX_ROWS=2000
LOG_DIR="${SCAN_SMITH_LOG_DIR:-$ROOT/logs}"
RECEIPT="$LOG_DIR/scan_smith.jsonl"

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    *) echo "usage: scan_smith.sh [--apply]" >&2; exit 2 ;;
  esac
done

if [ ! -d "$MIMOSA_DIR" ]; then
  echo "scan_smith: .mimosa dir missing: $MIMOSA_DIR" >&2
  exit 2
fi

now=$(date +%s)
age_cut=$((now - AGE_DAYS * 86400))

age_list=()

for d in $CACHE_DIRS; do
  for keep in $KEEP_DIRS; do
    if [ "$d" = "$keep" ]; then
      echo "scan_smith: refusing to age keep-side dir: $d" >&2
      exit 2
    fi
  done
  target="$MIMOSA_DIR/$d"
  [ -d "$target" ] || continue
  while IFS= read -r -d '' f; do
    m=$(stat -c %Y "$f" 2>/dev/null || echo "$now")
    case "$f" in
      *.gz|*.zip) continue ;;
    esac
    if [ "$m" -lt "$age_cut" ]; then
      age_list+=("$f")
    fi
  done < <(find "$target" -type f -print0 2>/dev/null | sort -z)
done

n_age=${#age_list[@]}

for f in "${age_list[@]}"; do
  age_days=$(( (now - $(stat -c %Y "$f")) / 86400 ))
  if [ "$APPLY" -eq 1 ]; then
    gzip -9 -f "$f"
    echo "aged: $f.gz (${age_days}d old)"
  else
    echo "would age: $f (${age_days}d old)"
  fi
done

if [ "$n_age" -eq 0 ]; then
  echo "scan_smith: nothing to age under $MIMOSA_DIR (cache dirs: $CACHE_DIRS)"
fi
echo "scan_smith: keep side untouched: $KEEP_DIRS (+ any dir outside the cache list)"

if [ "$APPLY" -eq 1 ]; then
  mkdir -p "$LOG_DIR"
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  dir_json="${MIMOSA_DIR//\\/\\\\}"  # escape backslashes so the receipt row is valid JSON on any OS
  printf '{"kind":"scan_smith","ts":"%s","mode":"apply","mimosa_dir":"%s","aged":%d}\n' \
    "$ts" "$dir_json" "$n_age" >> "$RECEIPT"
  # Receipt is itself retention-bound (2000 rows), per lane T b.
  tail -n "$RECEIPT_MAX_ROWS" "$RECEIPT" > "$RECEIPT.tmp" && mv "$RECEIPT.tmp" "$RECEIPT"
fi

exit 0
