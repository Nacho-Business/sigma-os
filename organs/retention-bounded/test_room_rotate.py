"""test_room_rotate.py - verifier for room_rotate.py (SIGMA wave 4 lane 20).

Consumer: room_rotate.py (size-bound rotation of the intake claim log; the
estate default is watch/room.jsonl, the release default is var/room.jsonl)
and the writer-side seam: every writer calls rr.maybe_rotate BEFORE each
append so the claim log stays under the size bound.
Covers: rotate-over-bound, fresh active file, gzipped-and-parseable archive,
counts preserved, partial tail row preserved, writer-side integration
(append, rotate, append), no-rotation-under-bound, dry-run default,
status census, archive-name collision guard.

Isolation: every test operates on tmp_path files only; no live log is ever
opened for write and never rotated.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ORGAN = Path(__file__).resolve().parent
if str(ORGAN) not in sys.path:
    sys.path.insert(0, str(ORGAN))

import room_rotate as rr  # noqa: E402


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_row(n: int, pad: int = 40) -> dict:
    return {"ts": f"2026-10-30T00:00:{n:02d}+00:00", "seat": "testseat",
            "type": "envelope_claim", "n": n, "pad": "x" * pad}


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def room(tmp_path) -> Path:
    return tmp_path / "room.jsonl"


class TestRotateOverBound:
    def test_over_bound_rotates_and_active_file_starts_fresh(self, room):
        write_rows(room, [make_row(i) for i in range(20)])
        assert room.stat().st_size > 400
        report = rr.maybe_rotate(room, bound=400, apply=True)
        assert report["rotated"] is True
        assert report["ok"] is True
        # active file starts fresh
        assert room.exists()
        assert room.stat().st_size == 0
        assert report["rows_after"] == 0
        assert report["live_bytes_after"] == 0

    def test_archive_is_gzipped_and_parseable_counts_preserved(self, room):
        rows = [make_row(i) for i in range(20)]
        write_rows(room, rows)
        report = rr.maybe_rotate(room, bound=400, apply=True)
        archive = Path(report["archive_path"])
        assert archive.is_file()
        assert archive.name.startswith("room.jsonl.")
        assert archive.name.endswith(".rotated")
        raw = archive.read_bytes()
        assert raw[:2] == b"\x1f\x8b", "archive must carry the gzip magic"
        with gzip.open(archive, "rb") as f:
            decoded = [json.loads(line) for line in f.read().decode("utf-8").splitlines() if line.strip()]
        assert len(decoded) == 20
        assert decoded[5]["n"] == 5
        # counts preserved: before == archived + live after
        assert report["rows_before"] == 20
        assert report["rows_archived"] == 20
        assert report["rows_after"] == 0
        assert report["gz_rows"] == 20

    def test_partial_tail_row_survives_in_live_file_not_archive(self, room):
        write_rows(room, [make_row(i) for i in range(10)])
        with room.open("a", encoding="utf-8") as f:
            f.write('{"ts": "half-written-row-no-newline')
        report = rr.maybe_rotate(room, bound=300, apply=True)
        assert report["rotated"] is True
        assert report["rows_before"] == 10  # the partial line is not a complete row
        assert report["rows_archived"] == 10
        assert report["rows_after"] == 0
        assert room.stat().st_size > 0  # partial bytes kept in the live file
        assert room.read_text(encoding="utf-8").startswith('{"ts": "half-written-row-no-newline')


class TestNoRotationUnderBound:
    def test_under_bound_is_noop(self, room):
        rows = [make_row(i) for i in range(3)]
        write_rows(room, rows)
        before = room.read_bytes()
        report = rr.maybe_rotate(room, bound=8 * 1024 * 1024, apply=True)
        assert report["rotated"] is False
        assert report["reason"] == "under_bound"
        assert room.read_bytes() == before
        assert len(list(room.parent.glob("room.jsonl.*.rotated"))) == 0

    def test_missing_file_is_noop(self, tmp_path):
        ghost = tmp_path / "nowhere" / "room.jsonl"
        report = rr.maybe_rotate(ghost, bound=10, apply=True)
        assert report["rotated"] is False
        assert report["reason"] == "missing"
        assert report["ok"] is True

    def test_zero_complete_rows_over_bound_is_noop(self, room):
        room.write_text("x" * 500, encoding="utf-8")
        report = rr.maybe_rotate(room, bound=100, apply=True)
        assert report["rotated"] is False
        assert report["reason"] == "no_complete_rows"
        assert room.stat().st_size == 500


class TestDryRunDefault:
    def test_cli_dry_run_writes_nothing(self, room, capsys):
        write_rows(room, [make_row(i) for i in range(20)])
        code = rr.main(["--path", str(room), "--bound-bytes", "400"])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert out["mode"] == "dry-run"
        assert out["would_rotate"] is True
        assert out["rotated"] is False
        assert room.stat().st_size > 0
        assert len(list(room.parent.glob("room.jsonl.*.rotated"))) == 0

    def test_cli_apply_rotates(self, room, capsys):
        write_rows(room, [make_row(i) for i in range(20)])
        code = rr.main(["--path", str(room), "--bound-bytes", "400", "--apply"])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert out["rotated"] is True
        assert room.stat().st_size == 0


class TestStatus:
    def test_status_reports_rows_bytes_rotations(self, room, capsys):
        write_rows(room, [make_row(i) for i in range(6)])
        rr.maybe_rotate(room, bound=200, apply=True)
        write_rows(room, [make_row(i) for i in range(100, 104)])
        code = rr.main(["--path", str(room), "--status"])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert out["mode"] == "status"
        assert out["rows"] == 4
        assert out["bytes"] == room.stat().st_size
        assert out["over_bound"] is False
        assert out["rotations_count"] == 1
        assert out["rotated_rows_total"] == 6
        assert out["rotations"][0]["rows"] == 6
        assert out["rotations"][0]["gz_bytes"] > 0

    def test_status_empty_room(self, tmp_path, capsys):
        code = rr.main(["--path", str(tmp_path / "room.jsonl"), "--status"])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert out["exists"] is False
        assert out["rows"] == 0
        assert out["rotations_count"] == 0


class TestWriterSeam:
    """The seam contract: a writer calls maybe_rotate before each append.

    The estate's named writer is intake_envelope.record_intake_receipt
    (tests/test_room_rotate.py there pins it against the real writer); this
    release pins the same contract against the reference writer shape so the
    seam, not one caller, is what is under test.
    """

    @pytest.fixture()
    def room(self, tmp_path) -> Path:
        return tmp_path / "room.jsonl"

    @staticmethod
    def seam_writer(path: Path, row: dict, bound: int) -> dict:
        # exactly the writer-side shape intake_envelope.record_intake_receipt uses
        rr.maybe_rotate(path, bound=bound, apply=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"status": "CLAIMED"}

    def test_append_rotate_append_cycle(self, room):
        # Pre-load the log past a tiny bound, as if written by earlier seats.
        write_rows(room, [make_row(i) for i in range(30)])
        bound = room.stat().st_size - 1  # force over-bound on the next append check

        result = self.seam_writer(room, make_row(1), bound)
        assert result["status"] == "CLAIMED"
        # rotation fired before the append: pre-load rows are archived, active
        # file holds exactly the fresh claim row
        assert room.stat().st_size > 0
        live = read_rows(room)
        assert len(live) == 1
        assert live[0]["type"] == "envelope_claim"

        # second append rides under the (now generous) bound: no second rotation
        result2 = self.seam_writer(room, make_row(2), 8 * 1024 * 1024)
        assert result2["status"] == "CLAIMED"
        assert len(read_rows(room)) == 2

        st = rr.status(room, bound=8 * 1024 * 1024)
        assert st["rotations_count"] == 1
        assert st["rows"] == 2
        assert st["rotated_rows_total"] == 30

    def test_append_under_bound_never_rotates(self, room):
        self.seam_writer(room, make_row(7), 8 * 1024 * 1024)
        st = rr.status(room, bound=8 * 1024 * 1024)
        assert st["rotations_count"] == 0
        assert st["rows"] == 1


class TestArchiveNameCollision:
    def test_collision_gets_counter_suffix(self, room):
        write_rows(room, [make_row(i) for i in range(20)])
        report = rr.maybe_rotate(room, bound=400, apply=True)
        first = Path(report["archive_path"])
        assert first.is_file()
        # refill and rotate again; the stamp collision path must not overwrite
        write_rows(room, [make_row(i) for i in range(50, 70)])
        report2 = rr.maybe_rotate(room, bound=400, apply=True)
        second = Path(report2["archive_path"])
        assert second.is_file()
        assert second != first
        assert first.read_bytes() != second.read_bytes()
