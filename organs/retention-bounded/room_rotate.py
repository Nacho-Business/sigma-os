"""room_rotate.py - size-bound rotation for the intake claim log (estate: watch/room.jsonl, release default: var/room.jsonl) (2026-10-30).

CONSUMER LAW (Anti-Cow Clause, Organ 5):
  Consumer: the writer tools/intake_envelope.py (record_intake_receipt calls
    room_rotate.maybe_rotate BEFORE each append, so the claim log stays under
    the size bound); operators run
    .venv\\Scripts\\python.exe tools\\room_rotate.py --status / --apply.
  Verifier: tests/test_room_rotate.py (run: pytest -q tests/test_room_rotate.py).

Why: watch/room.jsonl is append-only across every seat (intake claims, chan
posts, board directives, read receipts) with no rotation, pruning, or size cap
anywhere in its writers. SIGMA3_01_INTAKE_ORGAN_20261030.md flagged the
retention bound as owed; this organ is that gated change.

Shape: rotates the whole active file into a sibling gzip archive
  watch/room.jsonl.<UTC stamp>.rotated and leaves the active file fresh
  (empty, ready for the next append). Readers are rotation-aware already:
  mag/board_tailer.py resets its byte cursor when the file shrinks under it
  ("log was rotated/truncated"). History stays discoverable next to the live
  file by globbing watch/room.jsonl.*.rotated.

Safety (same discipline as tools/rotate_kpi.py):
- CLI DEFAULT IS DRY RUN. Nothing is written unless --apply is passed.
- Snapshot boundary = last newline at start; a half-written tail row is
  excluded from the archive and survives in the live file (rows appended by
  concurrent writers between snapshot and truncate are preserved the same
  way: truncate_keep rewrites the live file as bytes[cut:]).
- Archive is written to a .part temp and atomically os.replace'd only after
  verification: decompressed sha256 must equal the source window sha256 and
  the decompressed newline count must equal the snapshot row count. Any
  mismatch aborts with the live file untouched.
- Counts preserved: rows_before == rows_archived + live rows after the
  rewrite (the remainder is normally the empty set; only a partial tail row
  or a concurrent append can occupy it). Every run reports all three counts.
- Never deletes anything; archive files accumulate until the operator prunes.

CLI:
  .venv\\Scripts\\python.exe tools\\room_rotate.py --status            # report only
  .venv\\Scripts\\python.exe tools\\room_rotate.py                    # dry-run plan
  .venv\\Scripts\\python.exe tools\\room_rotate.py --apply           # real rotation (writer calls the function directly)

HOUSE LAW: strictly zero unicode em-dashes; stdlib only.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOM = ROOT / "var" / "room.jsonl"
DEFAULT_BOUND_BYTES = 8 * 1024 * 1024  # 8 MiB, the SIGMA_W4_05 recommended bound
CHUNK = 8 * 1024 * 1024


def _snapshot_end(path: Path) -> int:
    """Byte offset just past the last newline (a half-written tail row is excluded)."""
    size = path.stat().st_size
    with path.open("rb") as f:
        pos = size
        while pos > 0:
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step)
            i = buf.rfind(b"\n")
            if i >= 0:
                return pos + i + 1
    return 0


def _count_newlines_stream(path: Path) -> int:
    """Complete-row count of a plain file: newline bytes seen while streaming."""
    rows = 0
    with path.open("rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            rows += buf.count(b"\n")
    return rows


def _count_newlines_gz(path: Path) -> int:
    rows = 0
    with gzip.open(path, "rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            rows += buf.count(b"\n")
    return rows


def _snapshot_facts(path: Path, bound: int) -> dict:
    """Shared read-only facts for status and planning. Never writes."""
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "bytes": 0,
            "rows": 0,
            "partial_tail": False,
            "bound_bytes": bound,
            "over_bound": False,
            "headroom_bytes": bound,
        }
    size = path.stat().st_size
    return {
        "path": str(path),
        "exists": True,
        "bytes": size,
        "rows": _count_newlines_stream(path),
        "partial_tail": size > 0 and not _ends_with_newline(path),
        "bound_bytes": bound,
        "over_bound": size > bound,
        "headroom_bytes": max(bound - size, 0),
    }


def _ends_with_newline(path: Path) -> bool:
    size = path.stat().st_size
    if size == 0:
        return True
    with path.open("rb") as f:
        f.seek(size - 1)
        return f.read(1) == b"\n"


def _archive_name(path: Path) -> Path:
    """room.jsonl.<UTC stamp>.rotated next to the live file; collision-safe."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.parent / f"{path.name}.{stamp}.rotated"
    n = 2
    while candidate.exists():
        candidate = path.parent / f"{path.name}.{stamp}_{n}.rotated"
        n += 1
    return candidate


def archive_and_verify(path: Path, cut: int, expect_rows: int, dest: Path) -> dict:
    """Gzip bytes[0:cut] to dest via .part, verify sha256 and row count, publish atomically."""
    h_src = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    left = cut
    with path.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as gz:
        while left > 0:
            buf = src.read(min(CHUNK, left))
            if not buf:
                break
            h_src.update(buf)
            gz.write(buf)
            left -= len(buf)
    if left != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"short read: {left} bytes not copied")
    h_gz = hashlib.sha256()
    gz_rows = 0
    with gzip.open(tmp, "rb") as gz:
        while True:
            buf = gz.read(CHUNK)
            if not buf:
                break
            h_gz.update(buf)
            gz_rows += buf.count(b"\n")
    if gz_rows != expect_rows or h_gz.hexdigest() != h_src.hexdigest():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"verify FAILED: gz_rows={gz_rows} expect={expect_rows} "
            f"sha_match={h_gz.hexdigest() == h_src.hexdigest()}"
        )
    os.replace(tmp, dest)
    return {"gz_rows": gz_rows, "sha256": h_src.hexdigest(), "gz_bytes": dest.stat().st_size}


def truncate_keep(path: Path, cut: int) -> int:
    """Rewrite the live file in place as bytes[cut:] (partial tail row + concurrent appends)."""
    with path.open("r+b") as f:
        f.seek(cut)
        rest = f.read()
        f.seek(0)
        f.write(rest)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())
    return len(rest)


def maybe_rotate(path: Path = ROOM, bound: int = DEFAULT_BOUND_BYTES, apply: bool = True) -> dict:
    """The writer-side seam: rotate when the live file is over the size bound.

    Under bound, missing, or zero complete rows -> no-op report, live file
    untouched. Over bound -> archive-verify-truncate with counts preserved.
    A hard failure (I/O error, verification mismatch) raises: the caller
    (intake_envelope.record_intake_receipt) treats it in the same class as an
    unwritable log, with no swallowed fallback branch.
    """
    facts = _snapshot_facts(path, bound)
    report: dict = dict(facts)
    report["mode"] = "apply" if apply else "dry-run"
    if not facts["exists"]:
        report.update({"rotated": False, "reason": "missing", "ok": True})
        return report
    if not facts["over_bound"]:
        report.update({"rotated": False, "reason": "under_bound", "ok": True})
        return report
    end = _snapshot_end(path)
    rows_before = _count_newlines_stream(path)
    if end == 0:
        report.update({
            "rotated": False,
            "reason": "no_complete_rows",
            "rows_before": rows_before,
            "ok": True,
        })
        return report
    dest = _archive_name(path)
    report["rows_before"] = rows_before
    report["bytes_before"] = facts["bytes"]
    report["archive_path"] = str(dest)
    report["archive_bytes_raw"] = end
    if not apply:
        report["would_rotate"] = True
        report["rotated"] = False
        report["ok"] = True
        report["note"] = "dry run: nothing written"
        return report
    v = archive_and_verify(path, end, rows_before, dest)
    report.update(v)
    report["rows_archived"] = v["gz_rows"]
    live_bytes_after = truncate_keep(path, end)
    report["live_bytes_after"] = live_bytes_after
    report["rows_after"] = _count_newlines_stream(path)
    report["rotated"] = True
    report["ok"] = True
    return report


def status(path: Path = ROOM, bound: int = DEFAULT_BOUND_BYTES) -> dict:
    """Report rows/bytes of the live file plus the rotation census. Read-only."""
    out = _snapshot_facts(path, bound)
    pattern = f"{path.name}.*.rotated"
    rotations = []
    for p in sorted(path.parent.glob(pattern)):
        if p.name.endswith(".part"):
            continue
        rotations.append({
            "path": str(p),
            "gz_bytes": p.stat().st_size,
            "rows": _count_newlines_gz(p),
        })
    out["mode"] = "status"
    out["rotations_count"] = len(rotations)
    out["rotations"] = rotations
    out["rotated_bytes_total"] = sum(r["gz_bytes"] for r in rotations)
    out["rotated_rows_total"] = sum(r["rows"] for r in rotations)
    out["ok"] = True
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", default=str(ROOM))
    ap.add_argument("--bound-bytes", type=int, default=DEFAULT_BOUND_BYTES,
                    help="rotate when the live file exceeds this many bytes (default 8 MiB)")
    ap.add_argument("--status", action="store_true",
                    help="report rows/bytes/rotations and exit; writes nothing")
    ap.add_argument("--apply", action="store_true",
                    help="perform the rotation; the default is a dry-run plan")
    a = ap.parse_args(argv)
    path = Path(a.path)
    try:
        if a.status:
            print(json.dumps(status(path, a.bound_bytes), indent=2))
            return 0
        report = maybe_rotate(path, a.bound_bytes, apply=a.apply)
        print(json.dumps(report, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - one report surface for hard failures
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
